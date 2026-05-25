import os
import csv
import json
import requests
import subprocess

DATA_DIR = "data"
BLOB_DIR = os.path.join(DATA_DIR, "blobs")
TARGET_ALIAS = "tgtOrg"

access_token = None
instance_url = None
api_version = None
headers = {}

def get_latest_api_version(instance_url):
    resp = requests.get(f"{instance_url}/services/data/")
    if resp.status_code == 200:
        versions = resp.json()
        return versions[-1]["version"]  # The last one is the latest
    else:
        raise Exception(f"Failed to retrieve API versions: {resp.status_code} - {resp.text}")

# === Auth + Org Info ===
def get_auth():
    result = subprocess.run(
        ["sf", "org", "display", "--target-org", TARGET_ALIAS, "--json"],
        check=True,
        capture_output=True,
        text=True
    )
    info = json.loads(result.stdout)["result"]
    return info["accessToken"], info["instanceUrl"]

# === CSV Loader ===
def read_csv(filename):
    with open(os.path.join(DATA_DIR, filename), newline="") as f:
        return list(csv.DictReader(f))

def chunks(items, size=100):
    """Yield deterministic, non-empty chunks from an iterable."""
    clean_items = sorted({str(item).strip() for item in items if item is not None and str(item).strip()})
    for i in range(0, len(clean_items), size):
        yield clean_items[i:i + size]

def soql_quote(value):
    """Quote and escape a value for use inside a SOQL IN list."""
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"

def soql_in_list(values):
    return ",".join(soql_quote(value) for value in values)

def query_all_records(query_url, soql):
    """Run a SOQL query and follow nextRecordsUrl if Salesforce paginates results."""
    records = []

    resp = requests.get(query_url, headers=headers, params={"q": soql})
    if resp.status_code != 200:
        print(f"❌ Query failed: {resp.status_code} - {resp.text}")
        print("SOQL:", soql.strip())
        return records

    payload = resp.json()
    records.extend(payload.get("records", []))

    while not payload.get("done", True) and payload.get("nextRecordsUrl"):
        next_url = instance_url + payload["nextRecordsUrl"]
        resp = requests.get(next_url, headers=headers)
        if resp.status_code != 200:
            print(f"❌ QueryMore failed: {resp.status_code} - {resp.text}")
            break

        payload = resp.json()
        records.extend(payload.get("records", []))

    return records

def query_in_chunks(query_url, values, build_soql, label, chunk_size=100):
    """Run the same logical IN query in smaller batches to avoid 414 URI Too Long."""
    all_records = []
    batches = list(chunks(values, chunk_size))

    if not batches:
        print(f"ℹ️ No {label} values to query.")
        return all_records

    for idx, batch in enumerate(batches, start=1):
        print(f"📡 Querying {label} batch {idx}/{len(batches)} ({len(batch)} values)")
        all_records.extend(query_all_records(query_url, build_soql(batch)))

    return all_records

# === REST: POST ===
def create_record(obj_name, record, access_token, instance_url, api_version):
    url = f"{instance_url}/services/data/v{api_version}/sobjects/{obj_name}/"

    record.pop("Id", None)
    resp = requests.post(url, headers=headers, json=record)
    if resp.status_code == 201:
        print(f"✅ Created {obj_name} → {record.get('Name', record.get('ApiName', '') )}")
        return resp.json()["id"]
    else:
        print(f"❌ Failed {obj_name}: {resp.status_code} - {resp.text}")
        return None

def upsert_expression_set(record, access_token, instance_url, api_version):
    obj_name = "ExpressionSet"
    api_name = record.get("ApiName")
    if not api_name:
        print("❌ ExpressionSet record missing ApiName. Skipping.")
        return None

    # Query to see if the ExpressionSet exists
    query_url = f"{instance_url}/services/data/v{api_version}/query"
    soql = f"SELECT Id FROM {obj_name} WHERE ApiName = '{api_name}'"
    resp = requests.get(query_url, headers=headers, params={"q": soql})

    if resp.status_code != 200:
        print(f"❌ Failed to query for ExpressionSet {api_name}: {resp.status_code} - {resp.text}")
        return None

    records = resp.json().get("records", [])
    record.pop("ExpressionSetDefinitionId", None)

    if records:
        # UPDATE (PATCH)
        record_id = records[0]["Id"]
        patch_url = f"{instance_url}/services/data/v{api_version}/sobjects/{obj_name}/{record_id}"
        record.pop("ApiName", None)  # Don't include ApiName in the body
        patch_resp = requests.patch(patch_url, headers=headers, json=record)
        record["ApiName"] = api_name  # 👈 Put it back
        if patch_resp.status_code in [204, 200]:
            print(f"🔁 Updated ExpressionSet → {api_name}")
            return record_id
        else:
            print(f"❌ Failed to update ExpressionSet: {patch_resp.status_code} - {patch_resp.text}")
            return None
    else:
        # CREATE (POST)
        print(f"➕ Creating new ExpressionSet → {api_name}")
        return create_record(obj_name, record, access_token, instance_url, api_version)


def upsert_esdcd(record, access_token, instance_url, api_version):
    obj_name = "ExpressionSetDefinitionContextDefinition"
    context_id = record.get("ContextDefinitionId")
    esd_id = record.get("ExpressionSetDefinitionId")

    if not context_id or not esd_id:
        print("❌ Missing ContextDefinitionId or ExpressionSetDefinitionId for ESDCD.")
        return None

    # Query for existence
    soql = f"""
        SELECT Id FROM {obj_name}
        WHERE ExpressionSetDefinitionId = '{esd_id}'
    """
    query_url = f"{instance_url}/services/data/v{api_version}/query"
    resp = requests.get(query_url, headers=headers, params={"q": soql.strip()})

    if resp.status_code != 200:
        print(f"❌ Query failed for ESDCD: {resp.status_code} - {resp.text}")
        return None

    found = resp.json().get("records", [])

    if found:
        record_id = found[0]["Id"]
        print("✅ ExpressionSetDefinitionContextDefinition already exists. Updating ContextDefinitionId...")

        # Only update ContextDefinitionId
        patch_url = f"{instance_url}/services/data/v{api_version}/sobjects/{obj_name}/{record_id}"
        patch_body = { "ContextDefinitionId": context_id }

        patch_resp = requests.patch(patch_url, headers=headers, json=patch_body)
        if patch_resp.status_code in [200, 204]:
            print(f"🔁 Updated ContextDefinitionId on existing ESDCD → {record_id}")
            return record_id
        else:
            print(f"❌ Failed to update ESDCD: {patch_resp.status_code} - {patch_resp.text}")
            return None

    print("➕ Creating ExpressionSetDefinitionContextDefinition")
    return create_record(obj_name, record, access_token, instance_url, api_version)


# === REST: PATCH blob ===
import base64

def upload_blob_via_patch(record_id, blob_path, access_token, instance_url, api_version):
    # Build the endpoint for the record (omitting the /ConstraintModel sub-path)
    url = f"{instance_url}/services/data/v{api_version}/sobjects/ExpressionSetDefinitionVersion/{record_id}"

    # Read blob as binary and base64 encode it
    with open(blob_path, "rb") as f:
        blob_data = f.read()
    encoded_blob = base64.b64encode(blob_data).decode("utf-8")
    # Prepare payload; the ConstraintModel field expects a base64 string.
    payload = {
        "ConstraintModel": encoded_blob
    }
    # Use PATCH to update the record
    resp = requests.patch(url, headers=headers, json=payload)
    if resp.status_code == 204:
        print(f"📦 Uploaded blob via PATCH → {record_id}")
    else:
        print(f"⚠️ Blob upload failed → {record_id}: {resp.status_code} - {resp.text}")

# === MAIN ===
def main():
    global access_token, instance_url, api_version, headers
    access_token, instance_url = get_auth()
    api_version = get_latest_api_version(instance_url)  # e.g., '64.0'
    print(f"API Version is: {api_version}")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # Load all input data
    esdv = read_csv("ExpressionSetDefinitionVersion.csv")[0]
    esdcd = read_csv("ExpressionSetDefinitionContextDefinition.csv")[0]
    ess = read_csv("ExpressionSet.csv")[0]
    esc_list = read_csv("ExpressionSetConstraintObj.csv")

    # === Insert ExpressionSet
    ess.pop("Id", None)
    #ess_id = create_record("ExpressionSet", ess, access_token, instance_url, api_version)
    ess_id = upsert_expression_set(ess, access_token, instance_url, api_version)
    if not ess_id:
        print("❌ Could not create or update ExpressionSet. Aborting.")
        return

    # Resolve ExpressionSetDefinitionVersion ID by DeveloperName
    devname = esdv["DeveloperName"]
    query_url = f"{instance_url}/services/data/v{api_version}/query"
    headers = { "Authorization": f"Bearer {access_token}" }
    q = f"SELECT Id FROM ExpressionSetDefinitionVersion WHERE DeveloperName = '{devname}'"
    resp = requests.get(query_url, headers=headers, params={"q": q})

    if resp.status_code != 200 or not resp.json().get("records"):
        print(f"❌ Could not find ExpressionSetDefinitionVersion for {devname}")
        return
    esdv_id = resp.json()["records"][0]["Id"]

    # === Insert ExpressionSetDefinitionContextDefinition
    apiname = ess["ApiName"]
    cd_apiname = esdcd.get("ContextDefinitionApiName", "").strip()
    if not cd_apiname:
        print("❌ Invalid ExpressionSetDefinitionContextDefinition: missing ContextDefinitionApiName.")
        print("⚠️ Please ensure your CML Expression Set is using an extended custom Context Definition.")
        return

    esdcd.pop("ContextDefinitionApiName", None)
    esdcd.pop("ExpressionSetApiName", None)
    # Resolve ContextDefinition ID by DeveloperName
    q = f"SELECT Id FROM ContextDefinition WHERE DeveloperName = '{cd_apiname}'"
    resp = requests.get(query_url, headers=headers, params={"q": q})

    if resp.status_code != 200 or not resp.json().get("records"):
        print(f"❌ Could not find ContextDefinition for {cd_apiname}")
        return
    cd_id = resp.json()["records"][0]["Id"]
    esdcd["ContextDefinitionId"] = cd_id

    # Resolve ExpressionSetDefinition ID by DeveloperName
    q = f"SELECT Id FROM ExpressionSetDefinition WHERE DeveloperName = '{apiname}'"
    resp = requests.get(query_url, headers=headers, params={"q": q})

    if resp.status_code != 200 or not resp.json().get("records"):
        print(f"❌ Could not find ExpressionSetDefinition for {apiname}")
        return
    esd_id = resp.json()["records"][0]["Id"]
    esdcd["ExpressionSetDefinitionId"] = esd_id

    #create_record("ExpressionSetDefinitionContextDefinition", esdcd, access_token, instance_url, api_version)
    upsert_esdcd(esdcd, access_token, instance_url, api_version)

    # === Build lookup maps for ReferenceObjectId resolution ===
    print("🔁 Building legacy ID to Unique Key (UK) maps...")

    legacy_to_uk = {}
    product_codes = set()
    classification_names = set()
    prc_parent_codes = set()

    # Product2
    for row in read_csv("Product2.csv"):
        legacy_id = row["Id"]
        code = row["ProductCode"]
        product_codes.add(code)
        legacy_to_uk[legacy_id] = code  # UK for Product2 is ProductCode

    # ProductClassification
    for row in read_csv("ProductClassification.csv"):
        legacy_id = row["Id"]
        name = row["Name"]
        classification_names.add(name)
        legacy_to_uk[legacy_id] = name  # UK for Classification is just Name

    # ProductRelatedComponent
    for row in read_csv("ProductRelatedComponent.csv"):
        legacy_id = row["Id"]
        uk = (
            row["ParentProduct.ProductCode"] + "|" +
            (row.get("ChildProduct.ProductCode") or "") + "|" +
            (row.get("ChildProductClassification.Name") or "") + "|" +
            (row.get("ProductRelationshipType.Name") or "") + "|" +
            (row.get("Sequence") or "")
        )
        prc_parent_codes.add(row["ParentProduct.ProductCode"])
        legacy_to_uk[legacy_id] = uk

    print("📡 Querying target org for new IDs...")

    headers = {"Authorization": f"Bearer {access_token}"}
    query_url = f"{instance_url}/services/data/v{api_version}/query"

    # Query target org for Product2 in chunks to avoid 414 URI Too Long
    prod_records = query_in_chunks(
        query_url=query_url,
        values=product_codes,
        label="Product2 ProductCode",
        build_soql=lambda batch: (
            "SELECT Id, Name, ProductCode "
            f"FROM Product2 WHERE ProductCode IN ({soql_in_list(batch)})"
        )
    )
    uk_to_targetId_prod = {r["ProductCode"]: r["Id"] for r in prod_records}

    # Query target org for ProductClassification in chunks to avoid 414 URI Too Long
    class_records = query_in_chunks(
        query_url=query_url,
        values=classification_names,
        label="ProductClassification Name",
        build_soql=lambda batch: (
            "SELECT Id, Name "
            f"FROM ProductClassification WHERE Name IN ({soql_in_list(batch)})"
        )
    )
    uk_to_targetId_class = {r["Name"]: r["Id"] for r in class_records}

    # Query target org for ProductRelatedComponent in chunks to avoid 414 URI Too Long
    prc_records = query_in_chunks(
        query_url=query_url,
        values=prc_parent_codes,
        label="ProductRelatedComponent ParentProduct.ProductCode",
        build_soql=lambda batch: f"""
            SELECT Id, ParentProduct.ProductCode, ChildProduct.ProductCode,
                   ChildProductClassification.Name, ProductRelationshipType.Name, Sequence
            FROM ProductRelatedComponent
            WHERE ParentProduct.ProductCode IN ({soql_in_list(batch)})
        """
    )
    uk_to_targetId_prc = {
        (
            r["ParentProduct"]["ProductCode"] + "|" +
            (r["ChildProduct"]["ProductCode"] if r.get("ChildProduct") else "") + "|" +
            (r["ChildProductClassification"]["Name"] if r.get("ChildProductClassification") else "") + "|" +
            (r["ProductRelationshipType"]["Name"] if r.get("ProductRelationshipType") else "") + "|" +
            (str(r["Sequence"]) if r.get("Sequence") is not None else "")
        ): r["Id"]
        for r in prc_records
        if r.get("ParentProduct")
    }

    print("🔁 Maps ready. Resolving ReferenceObjectIds...")


    # === Insert ExpressionSetConstraintObj
    print("📥 Importing ExpressionSetConstraintObj records...")

    # Step 1: Query all current ESC objects for the ExpressionSet
    esc_query = f"SELECT Id FROM ExpressionSetConstraintObj WHERE ExpressionSetId = '{ess_id}'"
    resp = requests.get(query_url, headers=headers, params={"q": esc_query})
    existing_esc_ids = [r["Id"] for r in resp.json().get("records", [])]

    import_failed = False
    new_count = 0

    for row in esc_list:
        row.pop("Id", None)
        row.pop("ExpressionSet.ApiName", None)
        row.pop("Name", None)
        row["ExpressionSetId"] = ess_id

        ref_id = row.get("ReferenceObjectId", "")
        resolved_id = None
        uk = legacy_to_uk.get(ref_id)

        if ref_id.startswith("01t"):
            resolved_id = uk_to_targetId_prod.get(uk)
        elif ref_id.startswith("11B") and uk_to_targetId_class:
            resolved_id = uk_to_targetId_class.get(uk)
        elif ref_id.startswith("0dS"):
            resolved_id = uk_to_targetId_prc.get(uk)

        if resolved_id:
            row["ReferenceObjectId"] = resolved_id
            if not create_record("ExpressionSetConstraintObj", row, access_token, instance_url, api_version):
                import_failed = True
            else:
                new_count += 1
        else:
            print(f"⚠️ Could not resolve ReferenceObjectId: {ref_id} → UK: {uk}")
            import_failed = True

    print(f"📊 {new_count} new ExpressionSetConstraintObj records created.")

    # Step 2: Decide whether to delete the old records
    if not import_failed:
        print(f"🗑️ Deleting {len(existing_esc_ids)} old ExpressionSetConstraintObj records...")
        for eid in existing_esc_ids:
            del_url = f"{instance_url}/services/data/v{api_version}/sobjects/ExpressionSetConstraintObj/{eid}"
            del_resp = requests.delete(del_url, headers=headers)
            if del_resp.status_code not in [200, 204]:
                print(f"⚠️ Failed to delete {eid}: {del_resp.status_code} - {del_resp.text}")
        print("✅ Old records deleted.")
    else:
        print("⛔ Import encountered errors. Skipping deletion of existing ExpressionSetConstraintObj records.")
        print("⚠️ Warning: Target org now contains a mix of old and new constraints. Manual cleanup may be needed.")


    # === Upload Blob
    version = esdv.get("VersionNumber")
    blob_file = os.path.join(BLOB_DIR, f"ESDV_{devname.replace('_V' + version, '')}_V{version}.ffxblob")
    if os.path.exists(blob_file):
        upload_blob_via_patch(esdv_id, blob_file, access_token, instance_url, api_version)
    else:
        print(f"⚠️ Blob file missing: {blob_file}")

if __name__ == "__main__":
    main()
