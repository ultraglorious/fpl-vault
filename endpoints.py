import os
import time
from pathlib import Path

import yaml

from api_client import APIClient
from database_manager import PYTHON_TO_DUCKDB, load_table_schemas, map_to_duckdb_types
from infer_endpoint_schema import _python_type_name, infer_response_schema
from pipeline import log_discovery

API_BASE = "https://fantasy.premierleague.com/api/"
SCHEMA_YML = "datasources.yml"
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.5"))
_limit_ids = os.getenv("LIMIT_IDS", "").lower() in ("1", "true", "yes")
MAX_IDS = int(os.getenv("MAX_IDS", "0")) if _limit_ids else None

TABLE_KEY_MAP = {
    "game_settings": {"type": "singleton"},
    "game_config": {"type": "singleton"},
    "element_stats": {"type": "unique_on", "columns": ["name"]},
    "status": {"type": "unique_on", "columns": ["event"]},
    "dream_team_team": {"type": "unique_on", "columns": ["event_id", "element"]},
    "element_summary_history": {"type": "unique_on", "columns": ["element", "fixture"]},
    "element_summary_history_past": {"type": "unique_on", "columns": ["element_id", "season_name"]},
}


def _append_to_datasources_yml(table_name: str, mapped: dict, endpoint: str) -> None:
    """Add a new table stanza to datasources.yml."""
    path = Path(SCHEMA_YML)
    if not path.exists():
        return

    with open(path, "r") as f:
        data = yaml.safe_load(f)

    pk_cols = [c["name"] for c in mapped["columns"] if c.get("primary_key")]
    primary_key = pk_cols[0] if len(pk_cols) == 1 else None
    key_info = TABLE_KEY_MAP.get(table_name)
    if key_info and key_info["type"] == "unique_on":
        primary_key = key_info["columns"][0] if len(key_info["columns"]) == 1 else None
    elif key_info and key_info["type"] == "singleton":
        primary_key = "!singleton"

    columns_yaml = []
    for col in mapped["columns"]:
        col_entry = {"name": col["name"], "data_type": col["data_type"]}
        tests = []
        if not col.get("nullable", True):
            tests.append("not_null")
        if col.get("unique"):
            tests.append("unique")
        if col.get("primary_key") and not primary_key:
            pass
        if tests:
            col_entry["tests"] = tests
        columns_yaml.append(col_entry)

    table_entry = {"name": table_name, "columns": columns_yaml}
    if primary_key:
        table_entry["primary_key"] = primary_key

    source = data.get("sources", [{}])[0]
    existing = [t["name"] for t in source.get("tables", [])]
    if table_name not in existing:
        source.setdefault("tables", []).append(table_entry)
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(f"  Added {table_name} to {SCHEMA_YML}")


def _detect_pk_columns(schema: dict, table_name: str) -> list[str]:
    pk_cols = [c["name"] for c in schema["columns"] if c.get("primary_key")]
    if pk_cols:
        return pk_cols
    key_info = TABLE_KEY_MAP.get(table_name)
    if key_info and key_info["type"] == "unique_on":
        return key_info["columns"]
    raise ValueError(f"No primary key found for table '{table_name}'")


def _json_columns(schema: dict) -> set[str]:
    return {c["name"] for c in schema["columns"] if c["data_type"] == "JSON"}


def _validate_and_log_schema(table_name: str, inferred: dict, yaml_schema: dict, endpoint: str) -> None:
    inferred_cols = {c["name"]: PYTHON_TO_DUCKDB.get(c["type"], c["type"]) for c in inferred["columns"]}
    yaml_cols = {c["name"]: c["data_type"] for c in yaml_schema["columns"]}

    for col in set(inferred_cols) - set(yaml_cols):
        print(f"  [DRIFT] {table_name}: new column '{col}' ({inferred_cols[col]})")
        log_discovery(endpoint, table_name, "new_column", column=col, type=inferred_cols[col])

    for col in set(yaml_cols) - set(inferred_cols):
        print(f"  [DRIFT] {table_name}: column '{col}' missing from API")
        log_discovery(endpoint, table_name, "missing_column", column=col)

    for name in set(inferred_cols) & set(yaml_cols):
        if inferred_cols[name] != yaml_cols[name]:
            print(f"  [DRIFT] {table_name}.{name}: API={inferred_cols[name]}, YAML={yaml_cols[name]}")
            log_discovery(endpoint, table_name, "type_change", column=name, was=inferred_cols[name], now=yaml_cols[name])


def _resolve_schema(table_name, inferred, yaml_schemas, endpoint):
    yaml_schema = yaml_schemas.get(table_name)
    if yaml_schema:
        _validate_and_log_schema(table_name, inferred, yaml_schema, endpoint)
        return yaml_schema
    print(f"  [WARN] {table_name} not in {SCHEMA_YML} — using inferred types")
    log_discovery(endpoint, table_name, "new_table", type="inferred")
    return map_to_duckdb_types(inferred)


def _response_key(table_name: str, table_prefix: str = "") -> str:
    """Reverse a table prefix to get the original API response key."""
    if table_prefix:
        return table_name[len(table_prefix) + 1:]
    return table_name


def _init_tables(tables, db, endpoint, extra_columns=None):
    yaml_schemas = load_table_schemas(SCHEMA_YML)

    for table in tables:
        table_name = table["table_name"]

        if db.table_exists(table_name):
            print(f"  [SKIP] {table_name} already exists")
            continue

        mapped = _resolve_schema(table_name, table, yaml_schemas, endpoint)

        if extra_columns:
            for col_name, col_type in extra_columns.items():
                mapped["columns"].append({
                    "name": col_name,
                    "data_type": col_type,
                    "nullable": False,
                })

        key_info = TABLE_KEY_MAP.get(table_name, {})
        pk_value = mapped.get("primary_key")

        if pk_value == "!singleton":
            mapped["columns"].insert(0, {
                "name": "id",
                "data_type": "INTEGER",
                "primary_key": True,
            })

        if key_info.get("type") == "unique_on":
            cols = key_info["columns"]
            if len(cols) == 1:
                for col in mapped["columns"]:
                    if col["name"] == cols[0]:
                        col["unique"] = True
            # Composite: create table first, then add UNIQUE INDEX

        db.create_table(mapped, execute=True)

        if key_info.get("type") == "unique_on" and len(key_info["columns"]) > 1:
            qualified = db._qualify(table_name)
            cols = ", ".join(key_info["columns"])
            try:
                db.conn.execute(
                    f"CREATE UNIQUE INDEX idx_{table_name}_unique "
                    f"ON {qualified} ({cols})"
                )
            except Exception:
                pass

        _append_to_datasources_yml(table_name, mapped, endpoint)


def _ingest_rows(tables, response, db, endpoint, extra_columns=None, table_prefix=""):
    yaml_schemas = load_table_schemas(SCHEMA_YML)

    for table in tables:
        table_name = table["table_name"]

        if not db.table_exists(table_name):
            raise RuntimeError(f"Table '{table_name}' does not exist. Run --init first.")

        mapped = _resolve_schema(table_name, table, yaml_schemas, endpoint)

        key = _response_key(table_name, table_prefix)
        rows = response[key]
        if isinstance(rows, dict):
            rows = [rows]
        elif not isinstance(rows, list):
            print(f"  Skipping {table_name}: not a list or dict (type={type(rows).__name__})")
            continue

        if extra_columns:
            for row in rows:
                row.update(extra_columns)
            for col_name, col_val in extra_columns.items():
                col_type = PYTHON_TO_DUCKDB.get(_python_type_name(col_val), "TEXT")
                qualified = db._qualify(table_name)
                try:
                    db.conn.execute(
                        f"ALTER TABLE {qualified} ADD COLUMN {col_name} {col_type}"
                    )
                except Exception:
                    pass
                mapped["columns"].append({
                    "name": col_name,
                    "data_type": col_type,
                    "nullable": False,
                })

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
            cols = key_info["columns"]
            if len(cols) == 1:
                for col in mapped["columns"]:
                    if col["name"] == cols[0]:
                        col["unique"] = True
            else:
                qualified = db._qualify(table_name)
                col_list = ", ".join(cols)
                try:
                    db.conn.execute(
                        f"CREATE UNIQUE INDEX idx_{table_name}_unique "
                        f"ON {qualified} ({col_list})"
                    )
                except Exception:
                    pass

        pk_columns = _detect_pk_columns(mapped, table_name)
        json_cols = _json_columns(mapped)

        db.upsert_rows(table_name, rows, pk_columns=pk_columns, json_columns=json_cols)


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = APIClient(base_url=API_BASE)
    return _client


def _normalize_response(response, endpoint):
    """Wrap a top-level list response as a dict keyed by the endpoint's last path segment.

    The fixtures/ endpoint returns a bare array — not a dict with a fixtures key.
    """
    if isinstance(response, list):
        key = endpoint.rstrip("/").split("/")[-1]
        return {key: response}
    return response


# --- Init functions (--init mode) ---

def init_bootstrap(db):
    client = _get_client()
    endpoint = "bootstrap-static"
    print(f"\nInit: {endpoint}")
    response = client.get(endpoint=endpoint)
    tables = infer_response_schema(response)
    _init_tables(tables, db, endpoint)


def init_simple(endpoint, db, table_prefix=None):
    print(f"\nInit: {endpoint}")
    response = _get_client().get(endpoint=endpoint)
    response = _normalize_response(response, endpoint)
    tables = infer_response_schema(response, table_prefix=table_prefix or "")
    _init_tables(tables, db, endpoint)


def init_event_live(db):
    endpoint_id = "event/1/live/"
    print(f"\nInit: event/{{id}}/live/ (sample: event 1)")
    response = _get_client().get(endpoint=endpoint_id)
    tables = infer_response_schema(response, table_prefix="event_live")
    _init_tables(tables, db, "event/{id}/live/", extra_columns={"event_id": "INTEGER"})


def init_dream_team(db):
    endpoint_id = "dream-team/1/"
    print(f"\nInit: dream-team/{{id}}/ (sample: event 1)")
    response = _get_client().get(endpoint=endpoint_id)
    tables = infer_response_schema(response, table_prefix="dream_team")
    _init_tables(tables, db, "dream-team/{id}/", extra_columns={"event_id": "INTEGER"})


def init_element_summary(db):
    endpoint_id = "element-summary/1/"
    print(f"\nInit: element-summary/{{id}}/ (sample: element 1)")
    response = _get_client().get(endpoint=endpoint_id)
    tables = infer_response_schema(response, table_prefix="element_summary")
    _init_tables(tables, db, "element-summary/{id}/", extra_columns={"element_id": "INTEGER"})


# --- Ingestion functions ---

def ingest_bootstrap(db):
    client = _get_client()
    endpoint = "bootstrap-static"
    print(f"\nIngesting: {endpoint}")
    response = client.get(endpoint=endpoint)
    tables = infer_response_schema(response)
    _ingest_rows(tables, response, db, endpoint)


def ingest_simple(endpoint, db, table_prefix=None):
    print(f"\nIngesting: {endpoint}")
    response = _get_client().get(endpoint=endpoint)
    response = _normalize_response(response, endpoint)
    tables = infer_response_schema(response, table_prefix=table_prefix or "")
    _ingest_rows(tables, response, db, endpoint, table_prefix=table_prefix or "")


def ingest_event_live(db):
    endpoint_label = "event/{id}/live/"
    event_ids = db.fetch_column("events", "id")
    if MAX_IDS:
        event_ids = event_ids[:MAX_IDS]
    print(f"\nIngesting: {endpoint_label} ({len(event_ids)} events)")
    client = _get_client()

    for i, eid in enumerate(event_ids):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        if i % 10 == 0:
            print(f"  {i + 1}/{len(event_ids)}...")
        response = client.get(endpoint=f"event/{eid}/live/")
        tables = infer_response_schema(response, table_prefix="event_live")
        _ingest_rows(tables, response, db, endpoint_label, extra_columns={"event_id": eid}, table_prefix="event_live")

    print(f"  {len(event_ids)}/{len(event_ids)} done")


def ingest_dream_team(db):
    endpoint_label = "dream-team/{id}/"
    event_ids = db.fetch_column("events", "id")
    if MAX_IDS:
        event_ids = event_ids[:MAX_IDS]
    print(f"\nIngesting: {endpoint_label} ({len(event_ids)} events)")
    client = _get_client()

    for i, eid in enumerate(event_ids):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        if i % 10 == 0:
            print(f"  {i + 1}/{len(event_ids)}...")
        response = client.get(endpoint=f"dream-team/{eid}/")
        tables = infer_response_schema(response, table_prefix="dream_team")
        _ingest_rows(tables, response, db, endpoint_label, extra_columns={"event_id": eid}, table_prefix="dream_team")

    print(f"  {len(event_ids)}/{len(event_ids)} done")


def ingest_element_summary(db):
    endpoint_label = "element-summary/{id}/"
    element_ids = db.fetch_column("elements", "id")
    if MAX_IDS:
        element_ids = element_ids[:MAX_IDS]
    print(f"\nIngesting: {endpoint_label} ({len(element_ids)} elements)")
    client = _get_client()

    for i, eid in enumerate(element_ids):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        if i % 50 == 0:
            print(f"  {i + 1}/{len(element_ids)}...")
        response = client.get(endpoint=f"element-summary/{eid}/")
        tables = infer_response_schema(response, table_prefix="element_summary")
        _ingest_rows(tables, response, db, endpoint_label, extra_columns={"element_id": eid}, table_prefix="element_summary")

    print(f"  {len(element_ids)}/{len(element_ids)} done")
