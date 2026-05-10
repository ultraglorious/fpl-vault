import re
from api_client import APIClient


class UnknownDataType(Exception):
    """Raised when a Python value doesn't map to any known type."""


ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _python_type_name(value):
    """Map a Python value to a type name string. Returns None for None values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        if ISO_DATETIME_RE.match(value):
            return "datetime"
        return "str"
    if isinstance(value, (dict, list)):
        return "jsonb"
    raise UnknownDataType(f"No type mapping for value: {type(value).__name__}")


def infer_record_schema(records: list) -> list[dict]:
    """Extract column definitions from a list of record dicts."""
    if not records:
        raise ValueError("Empty record list")
    if not isinstance(records[0], dict):
        raise ValueError("List elements must be dicts")

    first = records[0]
    columns = []

    for key, value in first.items():
        type_name = _python_type_name(value)

        if type_name is None:
            for i in range(1, min(len(records), 6)):
                v = records[i].get(key)
                type_name = _python_type_name(v)
                if type_name is not None:
                    break
            if type_name is None:
                type_name = "str"

        nullable = False
        for record in records:
            if record.get(key) is None:
                nullable = True
                break

        columns.append({
            "name": key,
            "type": type_name,
            "nullable": nullable,
        })

    return columns


def infer_response_schema(json_data: dict, table_prefix: str = "") -> list[dict]:
    """Walk top-level keys of an API response dict and produce a list of table schema dicts.

    If table_prefix is provided, table names are prefixed (e.g. ``element_summary_fixtures``
    for the ``fixtures`` key when ``table_prefix="element_summary"``).
    """
    if not json_data:
        raise ValueError("API response is empty")

    tables = []

    for key, value in json_data.items():
        if isinstance(value, list):
            if len(value) == 0:
                continue
            if isinstance(value[0], dict):
                columns = infer_record_schema(value)
                table_name = f"{table_prefix}_{key}" if table_prefix else key
                tables.append({"table_name": table_name, "columns": columns})
        elif isinstance(value, dict):
            wrapped = [value]
            columns = infer_record_schema(wrapped)
            table_name = f"{table_prefix}_{key}" if table_prefix else key
            tables.append({"table_name": table_name, "columns": columns})

    return tables


if __name__ == '__main__':
    from database_manager import map_to_duckdb_types, DatabaseManager

    client = APIClient(base_url='https://fantasy.premierleague.com/api/')
    print("Fetching bootstrap-static data...")
    response = client.get(endpoint='bootstrap-static')

    table_schemas = infer_response_schema(response)
    print(f"Detected {len(table_schemas)} tables.\n")

    for table in table_schemas:
        pg_table = map_to_duckdb_types(table)
        table_name = pg_table["table_name"]
        print(f"-- Table: {table_name} ({len(pg_table['columns'])} columns)")
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.create_table(pg_table)
        print()
