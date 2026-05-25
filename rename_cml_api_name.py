#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path


DATA_DIR = Path("data")
BLOB_DIR = DATA_DIR / "blobs"


CSV_REPLACEMENTS = {
    "ExpressionSet.csv": [
        "ApiName",
        "Name",
    ],
    "ExpressionSetDefinitionVersion.csv": [
        "DeveloperName",
        "ExpressionSetDefinition.DeveloperName",
    ],
    "ExpressionSetDefinitionContextDefinition.csv": [
        "ExpressionSetApiName",
    ],
    "ExpressionSetConstraintObj.csv": [
        "ExpressionSet.ApiName",
    ],
}


def replace_exact(value, old_name, new_name):
    if value == old_name:
        return new_name
    if value == f"{old_name}_V1":
        return f"{new_name}_V1"
    return value


def planned_csv_changes(path, old_name, new_name):
    if not path.exists():
        return []

    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    changes = []
    columns = CSV_REPLACEMENTS.get(path.name, [])

    for row_index, row in enumerate(rows, start=2):
        for column in columns:
            if column not in row:
                continue

            old_value = row[column]
            new_value = replace_exact(old_value, old_name, new_name)

            # Version DeveloperName may be OLD_API_V1, OLD_API_V2, etc.
            if path.name == "ExpressionSetDefinitionVersion.csv" and column == "DeveloperName":
                new_value = old_value.replace(old_name, new_name)

            if new_value != old_value:
                changes.append((row_index, column, old_value, new_value))

    return changes


def update_csv(path, old_name, new_name):
    if not path.exists():
        print(f"⏭️  Missing {path}; skipping")
        return 0

    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    changed = 0
    columns = CSV_REPLACEMENTS.get(path.name, [])

    for row in rows:
        for column in columns:
            if column not in row:
                continue

            old_value = row[column]
            new_value = replace_exact(old_value, old_name, new_name)

            # Version DeveloperName may be OLD_API_V1, OLD_API_V2, etc.
            if path.name == "ExpressionSetDefinitionVersion.csv" and column == "DeveloperName":
                new_value = old_value.replace(old_name, new_name)

            if new_value != old_value:
                row[column] = new_value
                changed += 1

    if changed:
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    print(f"{'✅' if changed else '➖'} {path}: {changed} value(s) changed")
    return changed


def rename_blobs(old_name, new_name, dry_run):
    if not BLOB_DIR.exists():
        print(f"⏭️  Missing {BLOB_DIR}; skipping blob rename")
        return 0

    changed = 0
    for path in BLOB_DIR.iterdir():
        if not path.is_file():
            continue
        if old_name not in path.name:
            continue

        target = path.with_name(path.name.replace(old_name, new_name))
        if target.exists():
            raise FileExistsError(f"Target blob already exists: {target}")

        if dry_run:
            print(f"🔎 Would rename {path} -> {target}")
        else:
            path.rename(target)
            print(f"✅ Renamed {path} -> {target}")

        changed += 1

    if not changed:
        print("➖ No blob filenames matched")
    return changed


def main():
    parser = argparse.ArgumentParser(
        description="Rename exported CML API name references before import."
    )
    parser.add_argument("--from-name", required=True, help="Old CML API name, without _V suffix")
    parser.add_argument("--to-name", required=True, help="New CML API name, without _V suffix")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing files")
    args = parser.parse_args()

    old_name = args.from_name.strip()
    new_name = args.to_name.strip()

    if not old_name or not new_name:
        raise ValueError("--from-name and --to-name must be non-empty")
    if old_name == new_name:
        raise ValueError("--from-name and --to-name are the same")

    total = 0

    for filename in CSV_REPLACEMENTS:
        path = DATA_DIR / filename

        if args.dry_run:
            changes = planned_csv_changes(path, old_name, new_name)
            if not path.exists():
                print(f"⏭️  Missing {path}; skipping")
                continue

            for _, column, old_value, new_value in changes:
                print(f"🔎 Would update {path}:{column}: {old_value} -> {new_value}")

            print(f"{'🔎' if changes else '➖'} {path}: {len(changes)} value(s) would change")
            total += len(changes)
        else:
            total += update_csv(path, old_name, new_name)

    total += rename_blobs(old_name, new_name, args.dry_run)

    print(f"\nDone. {'Would change' if args.dry_run else 'Changed'} {total} item(s).")


if __name__ == "__main__":
    main()
