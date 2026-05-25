import subprocess
import requests
import csv
import os
import json
import argparse

# === Parse Arguments ===
parser = argparse.ArgumentParser(description="Export metadata/data for one Expression Set Definition & Version")
parser.add_argument("--developerName", type=str, required=True, help="DeveloperName of the Expression Set Definition (e.g. ProductQualification)")
parser.add_argument("--version", type=str, default="1", help="Version number (e.g. 1)")
parser.add_argument("--orgAlias", type=str, default="srcOrg", help="Salesforce org alias (default: srcOrg)")
args = parser.parse_args()

dev_name = args.developerName.strip()
version_num = args.version.strip()
api_name_versioned = f"{dev_name}_V{version_num}"

# single default source of truth
alias = args.orgAlias.strip()

# === API Version resolver helper ===
def get_latest_api_version(instance_url):
    resp = requests.get(f"{instance_url}/services/data/")
    if resp.status_code == 200:
        versions = resp.json()
        return versions[-1]["version"]  # Use latest version
    else:
        raise Exception(f"Failed to retrieve API versions: {resp.status_code} - {resp.text}")

# === Nested child reader helper ===
def get_field_value(rec, field):
    if "." in field:
        parent, child = field.split(".", 1)
        parent_obj = rec.get(parent)
        if parent_obj and isinstance(parent_obj, dict):
            return parent_obj.get(child, "")
        return ""
    return rec.get(field, "")

# === Export CSV Helper ===
def export_to_csv(query, filename, fields):
    print(f"📦 Exporting: {filename.replace('data/', '')}")
    print("🔍 SOQL Query:", query.strip())

    try:
        result = subprocess.run(
            ["sf", "org", "display", "--target-org", alias, "--json"],
            check=True,
            capture_output=True,
            text=True
        )
        org_info = json.loads(result.stdout)["result"]
        access_token = org_info["accessToken"]
        instance_url = org_info["instanceUrl"]
    except Exception as e:
        print("❌ Failed to retrieve org info from Salesforce CLI.")
        print(e)
        return

    api_version = get_latest_api_version(instance_url)
    endpoint = f"{instance_url}/services/data/v{api_version}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    response = requests.get(endpoint, headers=headers, params={"q": query})
    if response.status_code != 200:
        print(f"❌ API Error ({filename}): {response.status_code}")
        print(response.text)
        return

    payload = response.json()
    records = payload.get("records", [])
    while not payload.get("done", True) and payload.get("nextRecordsUrl"):
        next_url = instance_url + payload["nextRecordsUrl"]
        response = requests.get(next_url, headers=headers)
        if response.status_code != 200:
            print(f"❌ API Error ({filename}) while fetching next page: {response.status_code}")
            print(response.text)
            return
        payload = response.json()
        records.extend(payload.get("records", []))

    print(f"✅ {len(records)} records fetched for {filename}")

    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(fields)
        for rec in records:
            writer.writerow([get_field_value(rec, f) for f in fields])

    print(f"📄 Saved to {filename}\n")


# === Blob Download Helper ===
def download_constraint_model_blobs(input_csv="data/ExpressionSetDefinitionVersion.csv"):
    print("📥 Downloading ConstraintModel blobs...")

    try:
        result = subprocess.run(
            ["sf", "org", "display", "--target-org", alias, "--json"],
            check=True,
            capture_output=True,
            text=True
        )
        org_info = json.loads(result.stdout)["result"]
        access_token = org_info["accessToken"]
        instance_url = org_info["instanceUrl"]
        print(f"🔑 Auth success - instance: {instance_url}")
    except Exception as e:
        print("❌ Failed to get org info")
        print(e)
        return

    headers = {"Authorization": f"Bearer {access_token}"}
    os.makedirs("data/blobs", exist_ok=True)

    with open(input_csv, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            print(f"🧪 Row: DeveloperName={row.get('DeveloperName')}, Version={row.get('VersionNumber')}")

            if row.get("DeveloperName") != api_name_versioned:
                print("⏭️ Skipped (not matching filter)")
                continue

            blob_url = row.get("ConstraintModel", "")
            if not blob_url.startswith("/services"):
                print(f"⚠️ Invalid or empty blob URL: {blob_url}")
                continue

            full_url = instance_url + blob_url
            print(f"🌐 Fetching blob from: {full_url}")

            resp = requests.get(full_url, headers=headers)
            if resp.status_code == 200:
                file_path = f"data/blobs/ESDV_{dev_name}_V{version_num}.ffxblob"
                with open(file_path, "wb") as out_file:
                    out_file.write(resp.content)
                print(f"✅ Saved blob: {file_path}")
            else:
                print(f"❌ Failed to fetch blob: {resp.status_code} - {resp.text}")

# === Filtering Helper ===
def get_reference_ids_by_prefix(filename, prefix):
    ids = set()
    try:
        with open(filename, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                ref_id = row.get("ReferenceObjectId", "")
                if ref_id.startswith(prefix):
                    ids.add(ref_id)
    except Exception as e:
        print(f"❌ Could not process {filename} for prefix {prefix}: {e}")
    return sorted(ids)


# === Chunked Export Helpers ===
def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_org_connection():
    try:
        result = subprocess.run(
            ["sf", "org", "display", "--target-org", alias, "--json"],
            check=True,
            capture_output=True,
            text=True
        )
        org_info = json.loads(result.stdout)["result"]
        access_token = org_info["accessToken"]
        instance_url = org_info["instanceUrl"]
        api_version = get_latest_api_version(instance_url)
        endpoint = f"{instance_url}/services/data/v{api_version}/query"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        return endpoint, headers
    except Exception as e:
        print("❌ Failed to retrieve org info from Salesforce CLI.")
        print(e)
        return None, None


def export_ids_to_csv(obj_name, ids, select_clause, filename, fields, chunk_size=100):
    print(f"📦 Exporting: {filename.replace('data/', '')}")

    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(fields)

    if not ids:
        print(f"✅ 0 records fetched for {filename}")
        print(f"📄 Saved to {filename}\n")
        return

    endpoint, headers = get_org_connection()
    if not endpoint:
        return

    total_records = 0
    total_batches = ((len(ids) - 1) // chunk_size) + 1

    for batch_num, batch in enumerate(chunks(ids, chunk_size), start=1):
        joined = ",".join(f"'{x}'" for x in batch)
        query = f"""
            SELECT {select_clause}
            FROM {obj_name}
            WHERE Id IN ({joined})
        """

        print(f"🔍 {obj_name} batch {batch_num}/{total_batches}: {len(batch)} ids")
        response = requests.get(endpoint, headers=headers, params={"q": query})
        if response.status_code != 200:
            print(f"❌ API Error ({filename}, batch {batch_num}): {response.status_code}")
            print(response.text)
            continue

        records = response.json().get("records", [])
        total_records += len(records)

        with open(filename, mode="a", newline="") as file:
            writer = csv.writer(file)
            for rec in records:
                writer.writerow([get_field_value(rec, f) for f in fields])

    print(f"✅ {total_records} records fetched for {filename}")
    print(f"📄 Saved to {filename}\n")


# === Begin Export Tasks ===

export_to_csv(
    query=f"""
        SELECT ConstraintModel, DeveloperName, ExpressionSetDefinition.DeveloperName, ExpressionSetDefinitionId, Id, Language,
               MasterLabel, Status, VersionNumber
        FROM ExpressionSetDefinitionVersion
        WHERE ExpressionSetDefinition.DeveloperName = '{dev_name}'
        AND VersionNumber = {version_num}
    """,
    filename="data/ExpressionSetDefinitionVersion.csv",
    fields=[
        "ConstraintModel", "DeveloperName", "ExpressionSetDefinition.DeveloperName", "ExpressionSetDefinitionId", "Id", "Language",
        "MasterLabel", "Status", "VersionNumber"
    ]
)

export_to_csv(
    query=f"""
        SELECT ContextDefinitionApiName, ContextDefinitionId, ExpressionSetApiName, ExpressionSetDefinitionId
        FROM ExpressionSetDefinitionContextDefinition
        WHERE ExpressionSetDefinition.DeveloperName = '{dev_name}'
    """,
    filename="data/ExpressionSetDefinitionContextDefinition.csv",
    fields=[
        "ContextDefinitionApiName", "ContextDefinitionId", "ExpressionSetApiName", "ExpressionSetDefinitionId"
    ]
)

export_to_csv(
    query=f"""
        SELECT ApiName, Description, ExpressionSetDefinitionId, Id,
               InterfaceSourceType, Name, ResourceInitializationType, UsageType
        FROM ExpressionSet
        WHERE ExpressionSetDefinition.DeveloperName = '{dev_name}'
    """,
    filename="data/ExpressionSet.csv",
    fields=[
        "ApiName", "Description", "ExpressionSetDefinitionId", "Id",
        "InterfaceSourceType", "Name", "ResourceInitializationType", "UsageType"
    ]
)

export_to_csv(
    query=f"""
        SELECT Name, ExpressionSetId, ExpressionSet.ApiName, ReferenceObjectId, ConstraintModelTag, ConstraintModelTagType
        FROM ExpressionSetConstraintObj
        WHERE ExpressionSet.ApiName = '{dev_name}'
    """,
    filename="data/ExpressionSetConstraintObj.csv",
    fields=["Name", "ExpressionSetId", "ExpressionSet.ApiName", "ReferenceObjectId", "ConstraintModelTag", "ConstraintModelTagType"]
)

# === Supporting Objects ===
# === Pull only referenced Product2, ProductClassification, and ProductRelatedComponent ===
print("🔍 Filtering ReferenceObjectIds...")

product_ids = get_reference_ids_by_prefix("data/ExpressionSetConstraintObj.csv", "01t")
classification_ids = get_reference_ids_by_prefix("data/ExpressionSetConstraintObj.csv", "11B")
component_ids = get_reference_ids_by_prefix("data/ExpressionSetConstraintObj.csv", "0dS")

# Export referenced Product2 in chunks to avoid 414 URI Too Long
export_ids_to_csv(
    obj_name="Product2",
    ids=product_ids,
    select_clause="Id, Name, ProductCode",
    filename="data/Product2.csv",
    fields=["Id", "Name", "ProductCode"]
)

# Export referenced ProductClassification in chunks to avoid 414 URI Too Long
export_ids_to_csv(
    obj_name="ProductClassification",
    ids=classification_ids,
    select_clause="Id, Name",
    filename="data/ProductClassification.csv",
    fields=["Id", "Name"]
)

# Export referenced ProductRelatedComponent in chunks to avoid 414 URI Too Long
export_ids_to_csv(
    obj_name="ProductRelatedComponent",
    ids=component_ids,
    select_clause="""
        Id, Name,
        ParentProductId, ParentProduct.Name, ParentProduct.ProductCode,
        ChildProductId, ChildProduct.Name, ChildProduct.ProductCode,
        ChildProductClassificationId, ChildProductClassification.Name,
        ProductRelationshipTypeId, ProductRelationshipType.Name, Sequence
    """,
    filename="data/ProductRelatedComponent.csv",
    fields=[
        "Id", "Name",
        "ParentProductId", "ParentProduct.Name", "ParentProduct.ProductCode",
        "ChildProductId", "ChildProduct.Name", "ChildProduct.ProductCode",
        "ChildProductClassificationId", "ChildProductClassification.Name",
        "ProductRelationshipTypeId", "ProductRelationshipType.Name", "Sequence"
    ]
)

# === Download Blob ===
download_constraint_model_blobs()
