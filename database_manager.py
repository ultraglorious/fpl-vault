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


def backup_db(db_path: str, backup_dir: str | None = None) -> str | None:
    """Create a timestamped backup. Returns the backup path or None if source doesn't exist."""
    if not os.path.exists(db_path):
        return None
    if backup_dir is None:
        backup_dir = os.path.join(os.getenv("DATA_DIR", "data"), "backups")
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


def _parse_table_yaml(data: dict) -> dict:
    """Parse a per-table YAML dict into a schema dict for create_table()."""
    table_name = data["name"]
    primary_key = data.get("primary_key")
    columns = []

    for col in data.get("columns", []):
        col_def: dict = {
            "name": col["name"],
            "data_type": col["data_type"],
        }
        tests = col.get("tests", [])
        col_def["nullable"] = "not_null" not in tests
        if primary_key is not None and col["name"] == primary_key:
            col_def["primary_key"] = True
        columns.append(col_def)

    schema_dict: dict = {"table_name": table_name, "columns": columns}
    if primary_key is not None:
        schema_dict["primary_key"] = primary_key
    return schema_dict


def load_table_schemas(schema_dir: str | Path = "schema/fpl_api") -> dict[str, dict]:
    """Load per-table YAML files from a directory. Returns schema dicts keyed by table name."""
    dir_path = Path(schema_dir)
    if not dir_path.exists():
        return {}

    schemas: dict[str, dict] = {}
    for yml_file in sorted(dir_path.glob("*.yml")):
        with open(yml_file, "r") as f:
            data = yaml.safe_load(f)
        if data is None:
            continue
        schema_dict = _parse_table_yaml(data)
        schemas[schema_dict["table_name"]] = schema_dict

    return schemas


def save_table_schema(schema_dir: str | Path, mapped: dict) -> None:
    """Write a single table schema to schema_dir/<table_name>.yml."""
    dir_path = Path(schema_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    table_name = mapped["table_name"]
    out: dict = {"name": table_name}
    pk = mapped.get("primary_key")
    if pk is None:
        pk_cols = [c["name"] for c in mapped["columns"] if c.get("primary_key")]
        if len(pk_cols) == 1:
            pk = pk_cols[0]
    if pk is not None:
        out["primary_key"] = pk

    columns = []
    for col in mapped["columns"]:
        col_out = {"name": col["name"], "data_type": col["data_type"]}
        tests = []
        if not col.get("nullable", True):
            tests.append("not_null")
        if col.get("unique"):
            tests.append("unique")
        if tests:
            col_out["tests"] = tests
        columns.append(col_out)
    out["columns"] = columns

    file_path = dir_path / f"{table_name}.yml"
    with open(file_path, "w") as f:
        yaml.dump(out, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print(f"  Wrote {file_path}")


class DatabaseManager:
    """Open a DuckDB connection and manage table creation, upsert, and queries."""

    def __init__(self, db_path: str, schema: str | None = None):
        self.db_path = db_path
        self.schema = schema
        self.conn = duckdb.connect(db_path)

    def close(self):
        """Close the DuckDB connection."""
        self.conn.close()

    def _qualify(self, table_name: str) -> str:
        """Return schema-qualified table name if a schema is set."""
        if self.schema:
            return f"{self.schema}.{table_name}"
        return table_name

    def create_table(self, schema: dict, execute: bool = False, if_not_exists: bool = True) -> str:
        """Generate (and optionally execute) a CREATE TABLE statement from a schema dict."""
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
        """Drop a table if it exists."""
        self.conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        print(f"Dropped {table_name} (if existed)")

    def insert_rows(self, table_name: str, rows: list[dict], json_columns: set[str] | None = None) -> int:
        """Insert rows with parameterized queries. Serializes json_columns to JSON strings."""
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
        """Insert or update rows using ON CONFLICT on the primary key columns."""
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

    def fetch_column(self, table_name: str, column: str) -> list:
        """Return all values from a single column as a flat list."""
        qualified = self._qualify(table_name)
        result = self.conn.execute(f"SELECT {column} FROM {qualified} ORDER BY {column}").fetchall()
        return [row[0] for row in result]

    def table_exists(self, table_name: str) -> bool:
        """Check whether a table exists in the database."""
        qualified = self._qualify(table_name)
        try:
            self.conn.execute(f"SELECT 1 FROM {qualified} LIMIT 0")
            return True
        except Exception:
            return False

    def execute_query(self, query: str) -> None:
        """Execute a raw SQL query against the database."""
        try:
            self.conn.execute(query)
        except Exception as e:
            raise Exception(f"Failed to execute: {query}") from e
