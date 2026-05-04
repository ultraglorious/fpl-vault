"""Ingest FPL API data into DuckDB.

Usage:
    uv run python ingest.py           # Target fpl_dev.duckdb (default)
    uv run python ingest.py --live    # Target fpl.duckdb with backup
"""

import sys

from api_client import APIClient
from database_manager import DatabaseManager, backup_db, map_to_duckdb_types
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


def ingest_endpoint(endpoint: str, db: DatabaseManager) -> None:
    client = APIClient(base_url="https://fantasy.premierleague.com/api/")
    print(f"\n{'='*60}")
    print(f"Ingesting: {endpoint}")
    print(f"{'='*60}")

    response = client.get(endpoint=endpoint)
    tables = infer_response_schema(response)

    for table in tables:
        mapped = map_to_duckdb_types(table)
        table_name = mapped["table_name"]

        rows = response[table_name]
        if isinstance(rows, dict):
            rows = [rows]
        elif not isinstance(rows, list):
            print(f"Skipping {table_name}: not a list or dict (type={type(rows).__name__})")
            continue

        key_info = TABLE_KEY_MAP.get(table_name, {})

        if key_info.get("type") == "singleton":
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
    db_path = "fpl.duckdb" if live else "fpl_dev.duckdb"

    if live:
        backup_db(db_path)

    db = DatabaseManager(db_path)
    print(f"Target database: {db_path}")

    try:
        for endpoint in ENDPOINTS:
            ingest_endpoint(endpoint, db)
        print(f"\nDone. Database: {db_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
