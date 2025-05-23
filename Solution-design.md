# Solution Design Document: Expression Set Configuration Migration

* **Version:** 1.0
* **Date:** May 23, 2025
* **Author:** SFDX Expert (AI)

## 1. Introduction and Purpose

This document outlines the solution design for migrating specific `ExpressionSetDefinitionVersion` configurations, their associated components, and related reference data from a source Salesforce org to a target Salesforce org. The process aims to accurately reconstruct the Expression Set configuration, including its complex lookup relationships, in the target environment.

The solution leverages a set of custom Python scripts (`export_cml.py`, `import_cml.py`) that orchestrate Salesforce CLI (`sf`) commands for authentication and Salesforce REST APIs for detailed data operations. Data is exchanged between the export and import phases using local CSV files and binary blob files.

## 2. Scope

### 2.1. In-Scope Objects for Migration

The following Salesforce objects are involved in the migration process from the source org and will be created/updated in the target org:

* **Core Expression Set Objects:**
    * `ExpressionSetDefinition`
    * `ExpressionSetDefinitionVersion` (including its associated `.ffxblob` binary data)
    * `ExpressionSetDefinitionContextDefinition`
    * `ExpressionSetConstraintObj`
* **Referenced "Master" Data (handled by an upsert-like mechanism based on `Name`):**
    * `Product2`
    * `ProductClassification`
    * `ProductRelatedComponent`

### 2.2. Out-of-Scope

* Migration of other unrelated metadata or data.
* Automated creation of custom fields or objects in the target org if they do not exist.
* User and profile migration (permissions are assumed to be handled separately).

## 3. Prerequisites

### 3.1. System Prerequisites

* Salesforce CLI (`sf`) installed and configured on the machine executing the scripts.
* Python 3 installed on the machine, along with the `requests` library (`pip install requests`).
* Authenticated `sf` CLI sessions (org aliases) configured for both the source Salesforce org and the target Salesforce org. The scripts default to using an alias named "sfAlias" for the source (configurable in `export_cml.py`'s helper function) and "tgtOrg" for the target (`TARGET_ALIAS` in `import_cml.py`).

### 3.2. Target Org Prerequisites

* The target Salesforce org must have an active `sf` CLI alias.
* The user account associated with the target org alias must have adequate permissions to:
    * Perform REST API calls.
    * Create, read, update, and delete records for all in-scope objects.
    * Upload files/blobs.
* All custom objects and custom fields involved in the migration (as listed in Scope 2.1) must exist in the target org with compatible API names and data types as in the source org. This solution does not create or modify schema.
* For reliable mapping of `Product2`, `ProductClassification`, and `ProductRelatedComponent` records, the `Name` field for these objects should ideally serve as a unique business key within the context of the data being migrated. If duplicate `Name`s exist for records that need to be distinctly referenced, the script's current logic (which may pick the first match or create a new one if no exact match by name is found) might lead to unintended mappings.

## 4. Solution Overview & Steps

The migration process is a two-phase operation orchestrated by Python scripts: Export from Source and Import to Target.
![Alt Text](/cml-scripts/files/Solution%20Overview%20%26%20Steps%20-%20visual%20selection.png)

### Phase 1: Export Data from Source Org (`export_cml.py`)

1.  **Initiation:**
    * The user executes `export_cml.py`, providing the `developerName` of the `ExpressionSetDefinition` and the specific `version` number to be migrated as command-line arguments.
2.  **Authentication:**
    * The script uses `sf org display --target-org <source_alias> --json` to retrieve the access token and instance URL for the source org.
3.  **Data Extraction:**
    * **Core Expression Set Data:** The script queries and exports the specified `ExpressionSetDefinition`, its target `ExpressionSetDefinitionVersion`, any related `ExpressionSetDefinitionContextDefinition` records, and all associated `ExpressionSetConstraintObj` records.
    * **Blob Export:** The `.ffxblob` binary data associated with the `ExpressionSetDefinitionVersion` is downloaded.
    * **Referenced Master Data Export:** For each `ExpressionSetConstraintObj`, the script inspects the `ReferenceObjectId` and `ConstraintModelTagType` fields. It collects all unique IDs from `ReferenceObjectId` and then queries the source org to export the actual records these IDs point to (specifically `Id` and `Name` for `Product2`, `ProductClassification`, and detailed fields for `ProductRelatedComponent`).
4.  **Output:**
    * All queried structured data is saved as CSV files (e.g., `ExpressionSetDefinition.csv`, `Product2.csv`, etc.) into a local `./data/` directory.
    * The blob file is saved into a local `./data/blobs/` directory.

### Phase 2: Import Data to Target Org (`import_cml.py`)

1.  **Initiation:**
    * The user executes `import_cml.py`. The script is configured to use a target org alias (default "tgtOrg").
2.  **Authentication:**
    * Similar to the export script, it uses `sf org display --target-org <target_alias> --json` to get session details for the target org.
3.  **Data Processing and Import:**
    * **Upsert Referenced Master Data:**
        * The script reads `Product2.csv`, `ProductClassification.csv`, and `ProductRelatedComponent.csv` from the `./data/` directory.
        * For each record in these CSVs, it queries the target org to see if a record with the same `Name` already exists.
        * If a match by `Name` is found, its target Salesforce ID is noted.
        * If no match is found, a new record is created in the target org using the data from the CSV, and its new target Salesforce ID is noted.
        * This process builds in-memory maps (e.g., `product_uk_map`) that store `Name` -> `Target Org ID` for these reference objects.
    * **Create/Update Core Expression Set Components:**
        * `ExpressionSetDefinition`: Created or updated in the target org based on `DeveloperName`. The target ID is captured.
        * `ExpressionSetDefinitionVersion`: Created or updated, linked to the target `ExpressionSetDefinition` ID. The target ID is captured.
        * `ExpressionSetDefinitionContextDefinition`: Created and linked to the target `ExpressionSetDefinitionVersion` ID.
    * **Recreate `ExpressionSetConstraintObj` Records (Full Replace Strategy):**
        * The script first identifies all existing `ExpressionSetConstraintObj` records in the target org linked to the target `ExpressionSetDefinitionVersionId`. These are marked for deletion.
        * It then iterates through the `ExpressionSetConstraintObj.csv` (from the source export). For each source constraint:
            * The `ExpressionSetId` is set to the target `ExpressionSetDefinitionVersionId`.
            * The `ReferenceObjectId` is resolved:
                * The `ConstraintModelTagType` indicates the type of object referenced (e.g., Product2).
                * The script uses the `ReferenceObject__r.Name` (the Name of the originally referenced object from the source data) to look up the corresponding *target Salesforce ID* from the in-memory maps created earlier (e.g., from `product_uk_map`).
                * This resolved *target ID* is populated into the `ReferenceObjectId` field for the new constraint.
            * The new `ExpressionSetConstraintObj` record is created in the target org via a REST API call.
        * If all new constraint records are created successfully, the script proceeds to delete the previously identified (old) `ExpressionSetConstraintObj` records from the target org. If any new constraint creation fails, the deletion of old records is skipped to prevent incomplete states, and a warning is issued.
    * **Upload Blob:** The corresponding `.ffxblob` file (from `./data/blobs/`) is uploaded and associated with the newly created/updated `ExpressionSetDefinitionVersion` in the target org via a REST API call.

## 5. Relationship Management

The key to this migration is how relationships are re-established in the target org:

* **Parent-Child Relationships:** Standard hierarchical relationships (e.g., `ExpressionSetDefinition` to `ExpressionSetDefinitionVersion`, `ExpressionSetDefinitionVersion` to `ExpressionSetConstraintObj`) are handled by creating/identifying the parent record in the target org first, obtaining its target Salesforce ID, and then using this target ID when creating the child records.
* **Lookup Relationships (`ReferenceObjectId`):** This is the most critical and complex relationship handled. Since Salesforce IDs are not portable between orgs, a business key mapping strategy is employed:
    1.  **Export:** The source `Id` and `Name` (and other relevant fields) of records referenced by `ReferenceObjectId` (like `Product2`, `ProductClassification`, `ProductRelatedComponent`) are explicitly exported.
    2.  **Import - Reference Data Upsert:** In the target org, the import script uses the `Name` field from the exported reference data CSVs to find existing records or create new ones. This establishes a link between the source object's `Name` and its corresponding `Id` in the target org.
    3.  **Import - Constraint Creation:** When creating `ExpressionSetConstraintObj` records in the target org, the script uses the `Name` of the record that `ReferenceObjectId` pointed to in the source. It then uses this `Name` to look up the *target Salesforce ID* (from the map built in the previous step) and populates `ReferenceObjectId` with this *target ID*.

## 6. Error Handling (as observed in scripts)

* The Python scripts use `check=True` with `subprocess.run` for `sf` commands, causing the script to exit if an `sf` command fails.
* REST API calls made via the `requests` library are checked for successful HTTP status codes (e.g., 200, 201, 204). Failures are reported.
* A specific safety mechanism exists for `ExpressionSetConstraintObj` import: if the creation of any new constraint record fails, the script refrains from deleting the pre-existing constraint records in the target org for that `ExpressionSetDefinitionVersion`, logging a warning. This prevents data loss in case of partial import success.

## 7. Key Assumptions

* The `Name` field of `Product2`, `ProductClassification`, and `ProductRelatedComponent` objects is sufficiently unique to be used as a reliable business key for mapping these records between the source and target orgs.
* The schema (objects, fields, data types) for all in-scope entities is consistent and compatible between the source and target orgs.
* The user executing the scripts has the necessary permissions in both source and target orgs.
* The Salesforce CLI (`sf`) is installed, configured with aliases, and authenticated.
* The machine executing the scripts has Python and the `requests` library installed.