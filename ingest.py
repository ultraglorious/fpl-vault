"""Ingest FPL API data into DuckDB.

Usage:
    uv run python ingest.py           # Target fpl_dev.duckdb (default)
    uv run python ingest.py --live    # Target fpl.duckdb with backup
"""

import sys

from api_client import APIClient
from database_manager import DatabaseManager, backup_db, load_table_schemas, map_to_duckdb_types
from infer_endpoint_schema import infer_response_schema

ENDPOINTS = ["bootstrap-static"]

# Tables without a natural 'id' column — we provide PK info here.
# singleton: prepend a synthetic id=1 column and wrap the dict in {"id": 1, ...}
# unique_on: add a UNIQUE constraint on these columns in the schema
TABLE_KEY_MAP = {
    "game_settings": {"type": "singleton"},
    "game_config": {"type": "singleton"},
    "element_stats": {"type": "unique_on", "columns": ["name"]},
}


def _detect_pk_columns(schema: dict, table_name: str) -> list[str]:
    """Determine the primary key columns for a table schema."""
    pk_cols = [c["name"] for c in schema["columns"] if c.get("primary_key")]
    if pk_cols:
        return pk_cols

    key_info = TABLE_KEY_MAP.get(table_name)
    if key_info and key_info["type"] == "unique_on":
        return key_info["columns"]

    raise ValueError(f"No primary key found for table '{table_name}' and no entry in TABLE_KEY_MAP")


def _json_columns(schema: dict) -> set[str]:
    return {c["name"] for c in schema["columns"] if c["data_type"] == "JSON"}


SCHEMA_YML = "datasources.yml"


def _validate_schema(table_name: str, inferred: dict, yaml_schema: dict) -> None:
    """Compare inferred columns against the YAML schema, print warnings on diffs."""
    inferred_cols = {c["name"]: c["type"] for c in inferred["columns"]}
    yaml_cols = {c["name"]: c["data_type"] for c in yaml_schema["columns"]}

    new_cols = set(inferred_cols) - set(yaml_cols)
    missing_cols = set(yaml_cols) - set(inferred_cols)
    type_diffs = []
    for name in set(inferred_cols) & set(yaml_cols):
        if inferred_cols[name] != yaml_cols[name]:
            # Convert DuckDB type back to Python type name for comparison
            type_diffs.append((name, inferred_cols[name], yaml_cols[name]))

    if new_cols:
        print(f"  [DRIFT] {table_name}: new columns in API not in datasources.yml: {new_cols}")
    if missing_cols:
        print(f"  [DRIFT] {table_name}: columns in datasources.yml missing from API: {missing_cols}")
    if type_diffs:
        for name, inf_type, yml_type in type_diffs:
            print(f"  [DRIFT] {table_name}.{name}: API has {inf_type}, datasources.yml has {yml_type}")


def ingest_endpoint(endpoint: str, db: DatabaseManager) -> None:
    client = APIClient(base_url="https://fantasy.premierleague.com/api/")
    print(f"\n{'='*60}")
    print(f"Ingesting: {endpoint}")
    print(f"{'='*60}")

    response = client.get(endpoint=endpoint)
    tables = infer_response_schema(response)

    yaml_schemas = load_table_schemas(SCHEMA_YML)

    for table in tables:
        table_name = table["table_name"]

        yaml_schema = yaml_schemas.get(table_name)
        if yaml_schema:
            mapped = yaml_schema
            _validate_schema(table_name, table, yaml_schema)
        else:
            print(f"  [WARN] {table_name} not in {SCHEMA_YML} — using inferred types")
            mapped = map_to_duckdb_types(table)

        rows = response[table_name]
        if isinstance(rows, dict):
            rows = [rows]
        elif not isinstance(rows, list):
            print(f"Skipping {table_name}: not a list or dict (type={type(rows).__name__})")
            continue

        key_info = TABLE_KEY_MAP.get(table_name, {})
        pk_value = mapped.get("primary_key")

        if pk_value == "!singleton":
            mapped["columns"].insert(0, {
                "name": "id",
                "data_type": "INTEGER",
                "primary_key": True,
            })
            rows = [{"id": 1, **rows[0]}]

        if key_info.get("type") == "unique_on":
            for col_name in key_info["columns"]:
                for col in mapped["columns"]:
                    if col["name"] == col_name:
                        col["unique"] = True

        pk_columns = _detect_pk_columns(mapped, table_name)
        json_cols = _json_columns(mapped)

        db.create_table(mapped, execute=True)
        db.upsert_rows(table_name, rows, pk_columns=pk_columns, json_columns=json_cols)


def main():
    live = "--live" in sys.argv
    db_path = "data/fpl.duckdb" if live else "data/fpl_dev.duckdb"

    if live:
        backup_db(db_path)

    db = DatabaseManager(db_path, schema="fpl_api")
    print(f"Target database: {db_path}")

    try:
        for endpoint in ENDPOINTS:
            ingest_endpoint(endpoint, db)
        print(f"\nDone. Database: {db_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
