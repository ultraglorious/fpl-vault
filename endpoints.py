import os
import time
from pathlib import Path

from api_client import APIClient
from database_manager import PYTHON_TO_DUCKDB, load_table_schemas, map_to_duckdb_types, save_table_schema
from infer_endpoint_schema import _python_type_name, infer_response_schema
from pipeline import log_discovery, update_task_progress

API_BASE = "https://fantasy.premierleague.com/api/"
SCHEMA_DIR = os.getenv("SCHEMA_DIR", "schema/fpl_api")
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


def _detect_pk_columns(schema: dict, table_name: str) -> list[str]:
    """Find primary key columns from the schema or TABLE_KEY_MAP fallback."""
    pk_cols = [c["name"] for c in schema["columns"] if c.get("primary_key")]
    if pk_cols:
        return pk_cols
    key_info = TABLE_KEY_MAP.get(table_name)
    if key_info and key_info["type"] == "unique_on":
        return key_info["columns"]
    raise ValueError(f"No primary key found for table '{table_name}'")


def _json_columns(schema: dict) -> set[str]:
    """Return the set of column names with JSON data type."""
    return {c["name"] for c in schema["columns"] if c["data_type"] == "JSON"}


def _normalize_dtype(dtype: str) -> str:
    """Normalize equivalent DuckDB type names so VARCHAR/TEXT don't trigger false drift."""
    return "VARCHAR" if dtype in ("TEXT", "VARCHAR") else dtype


def _validate_and_log_schema(table_name: str, inferred: dict, yaml_schema: dict, endpoint: str) -> None:
    """Compare inferred schema against YAML and log any drift to discovery.jsonl."""
    inferred_cols = {c["name"]: _normalize_dtype(PYTHON_TO_DUCKDB.get(c["type"], c["type"])) for c in inferred["columns"]}
    yaml_cols = {c["name"]: _normalize_dtype(c["data_type"]) for c in yaml_schema["columns"]}

    for col in set(inferred_cols) - set(yaml_cols):
        print(f"  [DRIFT] {table_name}: new column '{col}' ({inferred_cols[col]})")
        log_discovery(endpoint, table_name, "new_column", column=col, type=inferred_cols[col])

    for name in set(inferred_cols) & set(yaml_cols):
        if inferred_cols[name] != yaml_cols[name]:
            print(f"  [DRIFT] {table_name}.{name}: API={inferred_cols[name]}, YAML={yaml_cols[name]}")
            log_discovery(endpoint, table_name, "type_change", column=name, was=yaml_cols[name], now=inferred_cols[name])


def _resolve_schema(table_name, inferred, yaml_schemas, endpoint):
    """Return the YAML schema if available, otherwise fall back to inferred types."""
    yaml_schema = yaml_schemas.get(table_name)
    if yaml_schema:
        _validate_and_log_schema(table_name, inferred, yaml_schema, endpoint)
        return yaml_schema
    print(f"  [WARN] {table_name} not in {SCHEMA_DIR} — using inferred types")
    return map_to_duckdb_types(inferred)


def _response_key(table_name: str, table_prefix: str = "") -> str:
    """Reverse a table prefix to get the original API response key."""
    if table_prefix:
        return table_name[len(table_prefix) + 1:]
    return table_name


def _apply_key_map_modifiers(mapped, table_name, rows=None):
    """Inject synthetic columns and uniqueness from TABLE_KEY_MAP. Returns rows (may be wrapped for singletons)."""
    key_info = TABLE_KEY_MAP.get(table_name, {})
    pk_value = mapped.get("primary_key")

    if pk_value == "!singleton" or key_info.get("type") == "singleton":
        existing = {c["name"] for c in mapped["columns"]}
        if "id" not in existing:
            mapped["columns"].insert(0, {
                "name": "id",
                "data_type": "INTEGER",
                "primary_key": True,
            })
        if rows is not None:
            rows = [{"id": 1, **rows[0]}]

    if key_info.get("type") == "unique_on" and len(key_info["columns"]) == 1:
        col_name = key_info["columns"][0]
        for col in mapped["columns"]:
            if col["name"] == col_name:
                col["unique"] = True

    return rows


def _ensure_unique_index(db, table_name):
    """Create a composite UNIQUE INDEX for multi-column unique_on tables, if needed."""
    key_info = TABLE_KEY_MAP.get(table_name, {})
    if key_info.get("type") == "unique_on" and len(key_info["columns"]) > 1:
        qualified = db._qualify(table_name)
        cols = ", ".join(key_info["columns"])
        try:
            db.conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_unique "
                f"ON {qualified} ({cols})"
            )
        except Exception:
            pass


def _init_tables(tables, db, endpoint, extra_columns=None):
    """Create empty tables from inferred schemas if they don't already exist."""
    yaml_schemas = load_table_schemas(SCHEMA_DIR)

    for table in tables:
        table_name = table["table_name"]

        if db.table_exists(table_name):
            print(f"  [SKIP] {table_name} already exists")
            continue

        mapped = _resolve_schema(table_name, table, yaml_schemas, endpoint)

        if extra_columns:
            existing = {c["name"] for c in mapped["columns"]}
            for col_name, col_type in extra_columns.items():
                if col_name not in existing:
                    mapped["columns"].append({
                        "name": col_name,
                        "data_type": col_type,
                        "nullable": False,
                    })

        _apply_key_map_modifiers(mapped, table_name)
        db.create_table(mapped, execute=True)
        _ensure_unique_index(db, table_name)

        save_table_schema(SCHEMA_DIR, mapped)
        log_discovery(endpoint, table_name, "table_created")


def _ingest_rows(tables, response, db, endpoint, extra_columns=None, table_prefix=""):
    """Upsert API response rows into existing database tables."""
    yaml_schemas = load_table_schemas(SCHEMA_DIR)

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
            existing = {c["name"] for c in mapped["columns"]}
            for col_name, col_val in extra_columns.items():
                if col_name in existing:
                    continue
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

        rows = _apply_key_map_modifiers(mapped, table_name, rows)
        _ensure_unique_index(db, table_name)

        pk_columns = _detect_pk_columns(mapped, table_name)
        json_cols = _json_columns(mapped)

        db.upsert_rows(table_name, rows, pk_columns=pk_columns, json_columns=json_cols)


_client = None


def _get_client():
    """Return a lazily-created singleton APIClient with session reuse."""
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
    """--init: Infer schema and create tables for bootstrap-static."""
    client = _get_client()
    endpoint = "bootstrap-static"
    print(f"\nInit: {endpoint}")
    response = client.get(endpoint=endpoint)
    tables = infer_response_schema(response)
    _init_tables(tables, db, endpoint)


def init_simple(endpoint, db, table_prefix=None):
    """--init: Infer schema and create tables for a simple (non-parameterized) endpoint."""
    print(f"\nInit: {endpoint}")
    response = _get_client().get(endpoint=endpoint)
    response = _normalize_response(response, endpoint)
    tables = infer_response_schema(response, table_prefix=table_prefix or "")
    _init_tables(tables, db, endpoint)


def init_event_live(db):
    """--init: Create tables for event/{id}/live/ using a sample event."""
    endpoint_id = "event/1/live/"
    print(f"\nInit: event/{{id}}/live/ (sample: event 1)")
    response = _get_client().get(endpoint=endpoint_id)
    tables = infer_response_schema(response, table_prefix="event_live")
    _init_tables(tables, db, "event/{id}/live/", extra_columns={"event_id": "INTEGER"})


def init_dream_team(db):
    """--init: Create tables for dream-team/{id}/ using a sample event."""
    endpoint_id = "dream-team/1/"
    print(f"\nInit: dream-team/{{id}}/ (sample: event 1)")
    response = _get_client().get(endpoint=endpoint_id)
    tables = infer_response_schema(response, table_prefix="dream_team")
    _init_tables(tables, db, "dream-team/{id}/", extra_columns={"event_id": "INTEGER"})


def init_element_summary(db):
    """--init: Create tables for element-summary/{id}/ using a sample element."""
    endpoint_id = "element-summary/1/"
    print(f"\nInit: element-summary/{{id}}/ (sample: element 1)")
    response = _get_client().get(endpoint=endpoint_id)
    tables = infer_response_schema(response, table_prefix="element_summary")
    _init_tables(tables, db, "element-summary/{id}/", extra_columns={"element_id": "INTEGER"})


# --- Ingestion functions ---

def ingest_bootstrap(db):
    """Ingest bootstrap-static endpoint into the database."""
    client = _get_client()
    endpoint = "bootstrap-static"
    print(f"\nIngesting: {endpoint}")
    response = client.get(endpoint=endpoint)
    tables = infer_response_schema(response)
    _ingest_rows(tables, response, db, endpoint)


def ingest_simple(endpoint, db, table_prefix=None):
    """Ingest a simple (non-parameterized) endpoint into the database."""
    print(f"\nIngesting: {endpoint}")
    response = _get_client().get(endpoint=endpoint)
    response = _normalize_response(response, endpoint)
    tables = infer_response_schema(response, table_prefix=table_prefix or "")
    _ingest_rows(tables, response, db, endpoint, table_prefix=table_prefix or "")


def ingest_event_live(db):
    """Ingest event/{id}/live/ for all events (or capped by LIMIT_IDS/MAX_IDS)."""
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
        update_task_progress("event-live", i + 1, len(event_ids))

    print(f"  {len(event_ids)}/{len(event_ids)} done")


def ingest_dream_team(db):
    """Ingest dream-team/{id}/ for all events (or capped by LIMIT_IDS/MAX_IDS)."""
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
        update_task_progress("dream-team", i + 1, len(event_ids))

    print(f"  {len(event_ids)}/{len(event_ids)} done")


def ingest_element_summary(db):
    """Ingest element-summary/{id}/ for all elements (or capped by LIMIT_IDS/MAX_IDS)."""
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
        update_task_progress("element-summary", i + 1, len(element_ids))

    print(f"  {len(element_ids)}/{len(element_ids)} done")
