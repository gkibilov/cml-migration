### 🔄 Constraint Expression Set Migration Tool

This tool exports and imports **Constraint ExpressionSet metadata**, including blob files (CML), from one Salesforce org to another using Salesforce CLI + REST API. It support migration of a new CML as well as update of the existing one, but it assumes target org already has relevant Context Definition and PCM data like Attributes, Product Classifications, Products and Product Related Components. 

---

#### 🔐 Authenticate Orgs

```bash
sf auth:web:login --instance-url https://<source-instance>.salesforce.com -a srcOrg
sf auth:web:login --instance-url https://<target-instance>.salesforce.com -a tgtOrg
```

---

#### 📤 Export from Source Org

```bash
python export_cml.py --developerName Laptop_Pro_Bundle --orgAlias srcOrg
```

`--orgAlias` is optional and defaults to `srcOrg`.

Exports CSVs and blob files into the `data/` folder.

---

#### 🔧 Change CML API Name (optional)

```bash
python rename_cml_api_name.py --from-name Laptop_Pro_Bundle --to-name Laptop_Pro_Bundle_V1
```

Renames exported CML API/name references in case the target already has the original name and you want to preserve it.

> Note: if the target org already has an `ExpressionSet` with the same `Name`, manually rename that existing Expression Set in the target org or choose a different `--to-name` before import. The import script matches existing `ExpressionSet` records by `ApiName`, but Salesforce also enforces uniqueness on `Name`.

---

#### 📥 Import into Target Org

```bash
python import_cml.py
```

Import uses the target org alias hardcoded in `import_cml.py`:

```python
TARGET_ALIAS = "tgtOrg"
```

`import_cml.py` does not currently support `--orgAlias`.

Loads metadata, resolves references, and uploads blob to the target org.

---

#### 📁 Output Structure

- `data/ExpressionSet.csv`
- `data/ExpressionSetDefinitionVersion.csv`
- `data/ExpressionSetDefinitionContextDefinition.csv`
- `data/ExpressionSetConstraintObj.csv`
- `data/Product2.csv`
- `data/ProductClassification.csv`
- `data/ProductRelatedComponent.csv`
- `data/blobs/*.ffxblob`

---

#### 🔧 Requirements

- Python 3.9+
- Salesforce CLI (`sf`)
- Connected orgs with accessible metadata API

---

#### 📝 Notes on Matching and Large Exports

- Product references are resolved by `Product2.ProductCode`, not `Product2.Name`. This is intentional because product names may drift between sandbox and production, while product codes are expected to remain stable.
- Product Related Component references are matched using parent and child product codes, plus classification, relationship type, and sequence.
- Export supports large association sets by following Salesforce REST query pagination, so `ExpressionSetConstraintObj` exports are not limited to the first query page.
- Export/import also chunk large `IN (...)` lookups to avoid `414 URI Too Long` errors when resolving many products, classifications, or product related components.
- Import creates `ExpressionSetConstraintObj` records one at a time. This is slow for large models, but makes failures easier to identify and avoids silently skipping unresolved associations.

