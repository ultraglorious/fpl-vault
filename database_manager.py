import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import duckdb
import yaml


PYTHON_TO_DUCKDB: dict[str, str] = {
    "str": "TEXT",
    "int": "INTEGER",
    "float": "DOUBLE",
    "bool": "BOOLEAN",
    "jsonb": "JSON",
    "datetime": "TIMESTAMP",
}


def backup_db(db_path: str, backup_dir: str = "data/backups") -> str | None:
    """Create a timestamped backup. Returns the backup path or None if source doesn't exist."""
    if not os.path.exists(db_path):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    filename = os.path.basename(db_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"{filename}.{timestamp}.bak")
    shutil.copy2(db_path, backup_path)
    print(f"Backed up {db_path} -> {backup_path}")
    return backup_path


def map_to_duckdb_types(schema: dict) -> dict:
    """Convert a schema from Python type names to DuckDB type names."""
    columns = []
    for col in schema["columns"]:
        db_type = PYTHON_TO_DUCKDB.get(col["type"], "TEXT")
        new_col = {
            "name": col["name"],
            "data_type": db_type,
            "nullable": col.get("nullable", True),
        }
        if col["name"] == "id" and col["type"] == "int":
            new_col["primary_key"] = True
        columns.append(new_col)

    result = {"table_name": schema["table_name"], "columns": columns}
    if "foreign_keys" in schema:
        result["foreign_keys"] = schema["foreign_keys"]
    return result


def load_table_schemas(yml_path: str | Path) -> dict[str, dict]:
    """Read a dbt-format datasources.yml and return schema dicts for create_table().

    Returns a dict keyed by table name. Each value is a schema dict with
    ``table_name`` and ``columns``, compatible with DatabaseManager.create_table().
    The ``primary_key`` table-level field in the YAML is preserved as a top-level
    key on the schema dict (ingest.py uses it for upsert detection).
    """
    path = Path(yml_path)
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")

    with open(path, "r") as f:
        data = yaml.safe_load(f)

    schemas: dict[str, dict] = {}

    for source in data.get("sources", []):
        for table in source.get("tables", []):
            table_name = table["name"]
            primary_key = table.get("primary_key")
            columns = []

            for col in table.get("columns", []):
                col_def: dict = {
                    "name": col["name"],
                    "data_type": col["data_type"],
                }
                tests = col.get("tests", [])
                if "not_null" in tests:
                    col_def["nullable"] = False
                else:
                    col_def["nullable"] = True
                if primary_key is not None and col["name"] == primary_key:
                    col_def["primary_key"] = True
                columns.append(col_def)

            schema_dict: dict = {
                "table_name": table_name,
                "columns": columns,
            }
            if primary_key is not None:
                schema_dict["primary_key"] = primary_key
            schemas[table_name] = schema_dict

    return schemas


class DatabaseManager:
    def __init__(self, db_path: str = "data/fpl_dev.duckdb", schema: str | None = None):
        self.db_path = db_path
        self.schema = schema
        self.conn = duckdb.connect(db_path)

    def close(self):
        self.conn.close()

    def _qualify(self, table_name: str) -> str:
        """Return schema-qualified table name if a schema is set."""
        if self.schema:
            return f"{self.schema}.{table_name}"
        return table_name

    def create_table(self, schema: dict, execute: bool = False, if_not_exists: bool = True) -> str:
        table_name = schema["table_name"]
        lines = []

        for column in schema["columns"]:
            name = column["name"]
            data_type = column["data_type"]
            parts = [name, data_type]

            if not column.get("nullable", True):
                parts.append("NOT NULL")

            if column.get("unique", False):
                parts.append("UNIQUE")

            if column.get("primary_key", False):
                parts.append("PRIMARY KEY")

            if "default" in column:
                parts.append(f"DEFAULT {column['default']}")

            lines.append(" ".join(parts))

        for fk in schema.get("foreign_keys", []):
            fk_def = (
                f"FOREIGN KEY ({fk['column_name']}) "
                f"REFERENCES {fk['referenced_table']}({fk['referenced_column']})"
            )
            lines.append(fk_def)

        if_not_exists_clause = "IF NOT EXISTS " if if_not_exists else ""
        qualified = self._qualify(table_name)
        query = (
            f"CREATE TABLE {if_not_exists_clause}{qualified} (\n"
            f"  " + ",\n  ".join(lines) + "\n"
            f");"
        )

        print(query)
        if execute:
            if self.schema:
                self.conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            self.conn.execute(query)
            print(f"  -> created {table_name}")
        return query

    def drop_table(self, table_name: str) -> None:
        self.conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        print(f"Dropped {table_name} (if existed)")

    def insert_rows(self, table_name: str, rows: list[dict], json_columns: set[str] | None = None) -> int:
        if not rows:
            return 0

        if json_columns is None:
            json_columns = set()

        columns = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        query = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"

        values = []
        for row in rows:
            value_row = []
            for col in columns:
                val = row[col]
                if col in json_columns and isinstance(val, (dict, list)):
                    val = json.dumps(val)
                value_row.append(val)
            values.append(tuple(value_row))

        self.conn.executemany(query, values)
        print(f"Inserted {len(rows)} rows into {table_name}")
        return len(rows)

    def upsert_rows(self, table_name: str, rows: list[dict], pk_columns: list[str], json_columns: set[str] | None = None) -> int:
        if not rows:
            return 0

        if json_columns is None:
            json_columns = set()

        columns = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)

        update_cols = [c for c in columns if c not in pk_columns]
        set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
        pk_list = ", ".join(pk_columns)

        qualified = self._qualify(table_name)
        query = (
            f"INSERT INTO {qualified} ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT ({pk_list}) DO UPDATE SET {set_clause}"
        )

        values = []
        for row in rows:
            value_row = []
            for col in columns:
                val = row[col]
                if col in json_columns and isinstance(val, (dict, list)):
                    val = json.dumps(val)
                value_row.append(val)
            values.append(tuple(value_row))

        self.conn.executemany(query, values)
        print(f"Upserted {len(rows)} rows into {table_name}")
        return len(rows)

    def execute_query(self, query: str) -> None:
        try:
            self.conn.execute(query)
        except Exception as e:
            raise Exception(f"Failed to execute: {query}") from e
