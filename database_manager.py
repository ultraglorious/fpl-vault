import duckdb


PYTHON_TO_DUCKDB: dict[str, str] = {
    "str": "TEXT",
    "int": "INTEGER",
    "float": "DOUBLE",
    "bool": "BOOLEAN",
    "jsonb": "JSON",
    "datetime": "TIMESTAMP",
}


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


class DatabaseManager:
    def __init__(self, db_path: str = "fpl.duckdb"):
        self.conn = duckdb.connect(db_path)

    def close(self):
        self.conn.close()

    def create_table(self, schema: dict) -> None:
        table_name = schema["table_name"]
        lines = []

        for column in schema["columns"]:
            name = column["name"]
            data_type = column["data_type"]
            parts = [name, data_type]

            if not column.get("nullable", True):
                parts.append("NOT NULL")

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

        query = (
            f"CREATE TABLE {table_name} (\n"
            f"  " + ",\n  ".join(lines) + "\n"
            f");"
        )

        print(query)
        # self.conn.execute(query)

    def execute_query(self, query: str) -> None:
        try:
            self.conn.execute(query)
        except Exception as e:
            raise Exception(f"Failed to execute: {query}") from e
