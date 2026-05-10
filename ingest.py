"""Ingest FPL API data into DuckDB.

Usage:
    uv run python ingest.py           # Target footballdb_dev.duckdb (default)
    uv run python ingest.py --live    # Target footballdb.duckdb with backup
"""

import os
import sys

from dotenv import load_dotenv

from api_client import APIClient
from database_manager import DatabaseManager, PYTHON_TO_DUCKDB, backup_db, load_table_schemas, map_to_duckdb_types
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


SCHEMA_DIR = os.getenv("SCHEMA_DIR", "schema/fpl_api")


def _validate_schema(table_name: str, inferred: dict, yaml_schema: dict) -> None:
    """Compare inferred columns against the YAML schema, print warnings on diffs."""
    inferred_cols = {c["name"]: PYTHON_TO_DUCKDB.get(c["type"], c["type"]) for c in inferred["columns"]}
    yaml_cols = {c["name"]: c["data_type"] for c in yaml_schema["columns"]}

    new_cols = set(inferred_cols) - set(yaml_cols)
    missing_cols = set(yaml_cols) - set(inferred_cols)
    type_diffs = []
    for name in set(inferred_cols) & set(yaml_cols):
        if inferred_cols[name] != yaml_cols[name]:
            type_diffs.append((name, inferred_cols[name], yaml_cols[name]))

    if new_cols:
        print(f"  [DRIFT] {table_name}: new columns in API not in schema directory: {new_cols}")
    if missing_cols:
        print(f"  [DRIFT] {table_name}: columns in schema directory missing from API: {missing_cols}")
    if type_diffs:
        for name, inf_type, yml_type in type_diffs:
            print(f"  [DRIFT] {table_name}.{name}: API has {inf_type}, schema directory has {yml_type}")


def ingest_endpoint(endpoint: str, db: DatabaseManager) -> None:
    client = APIClient(base_url="https://fantasy.premierleague.com/api/")
    print(f"\n{'='*60}")
    print(f"Ingesting: {endpoint}")
    print(f"{'='*60}")

    response = client.get(endpoint=endpoint)
    tables = infer_response_schema(response)

    yaml_schemas = load_table_schemas(SCHEMA_DIR)

    for table in tables:
        table_name = table["table_name"]

        yaml_schema = yaml_schemas.get(table_name)
        if yaml_schema:
            mapped = yaml_schema
            _validate_schema(table_name, table, yaml_schema)
        else:
            print(f"  [WARN] {table_name} not in {SCHEMA_DIR} — using inferred types")
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
    load_dotenv()
    live = "--live" in sys.argv
    db_name = os.getenv("DB_NAME", "footballdb")
    data_dir = os.getenv("DATA_DIR", "data")
    suffix = "" if live else "_dev"
    db_path = f"{data_dir}/{db_name}{suffix}.duckdb"

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
