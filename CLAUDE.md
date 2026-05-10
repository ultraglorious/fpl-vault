# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```
uv sync                     # Install dependencies
uv run pytest               # Run all tests (83 tests)
uv run pytest -k "test_name"  # Run a single test
uv run python infer_endpoint_schema.py  # Print CREATE TABLE statements from live API
uv run python pipeline.py --init          # Infer schemas + create empty tables (dev DB)
uv run python pipeline.py --init --live   # Same against production DB
uv run python pipeline.py                 # Ingest all endpoints into dev DB
uv run python pipeline.py --live          # Ingest all endpoints into production DB (with backup)
uv run python pipeline.py --tasks fixtures,event-status  # Run specific tasks only
uv run python pipeline.py --init --tasks event-live     # Init a single parameterized endpoint
```

## Rules

- **Never** add Co-Authored-By, Signed-off-by, or similar trailer lines to git commits.
- **Never** `cd` to the current working directory in bash commands. The shell starts in the project root — just run the command directly.

## Configuration

All via `.env` (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DB_NAME` | `footballdb` | Database filename |
| `DATA_DIR` | `data` | Data and log directory |
| `SCHEMA_DIR` | `schema/fpl_api` | Per-table schema YAML files |
| `REQUEST_DELAY` | `0.5` | Seconds between API calls in parameterized loops |
| `LIMIT_IDS` | `false` | Enable ID capping for fast dev runs |
| `MAX_IDS` | `0` | Max IDs to process when `LIMIT_IDS` is on |

Dev appends `_dev` to the database name, live omits it and takes a backup first.
`.env.example` mirrors `.env` without secrets — keep both in sync when adding vars.

## Architecture

Ingests FPL data from undocumented public endpoints (`https://fantasy.premierleague.com/api/`) into a DuckDB database of player and team data. See `API_ENDPOINTS.md` for the full endpoint catalog.

Schema creation and data ingestion are separate steps. Run `--init` first to create empty tables from inferred schemas, then run normally to ingest data. Normal runs will fail if tables don't exist.

### Components

- **`pipeline.py`** — Lightweight DAG orchestrator. `Pipeline` class runs tasks in dependency order (topological sort via Kahn's algorithm). `Task` is a named callable `(db) -> None`. Tracks progress atomically in `data/pipeline_progress.json` and logs each execution to `data/pipeline_runs.jsonl`. Two modes: `--init` (schema + table creation) and normal (data ingestion). `--tasks` flag runs a named subset.
- **`endpoints.py`** — Ingestion functions for all endpoints. `init_*` functions handle schema inference and table creation. `ingest_*` functions handle data upsert. Parameterized endpoints loop over IDs from existing tables and inject the parameter as a column (e.g. `event_id`). Key internal helpers: `_resolve_schema()` prefers YAML over inferred types and logs drift via `_validate_and_log_schema()`, `_apply_key_map_modifiers()` handles singleton tables and composite unique indexes per `TABLE_KEY_MAP`.
- **`api_client.py`** — Thin wrapper around `requests` with session reuse, 3-retry exponential backoff, and 30s timeout.
- **`database_manager.py`** — `DatabaseManager(db_path)` opens a DuckDB connection. Key functions: `create_table()` (generate + optionally execute DDL), `upsert_rows()` (`INSERT … ON CONFLICT DO UPDATE`), `fetch_column()` (for parameterized loops), `table_exists()`, `backup_db()` (timestamped `.bak` copies). Module-level functions: `load_table_schemas(dir)` globs per-table YAML files from a directory, `save_table_schema(dir, schema)` writes one table to its own YAML file, `map_to_duckdb_types()` converts inferred Python types to DuckDB types.
- **`infer_endpoint_schema.py`** — Takes a JSON API response dict and produces table schemas. `infer_response_schema()` walks top-level keys with optional `table_prefix` for nested endpoints. `infer_record_schema()` extracts column names, types (with ISO datetime detection), and nullability from record arrays.

### Schema files

Table schemas live as individual YAML files under `schema/fpl_api/` (configurable via `SCHEMA_DIR`):

```yaml
name: teams
primary_key: id
columns:
  - name: id
    data_type: INTEGER
    tests: [not_null]
```

`--init` writes them; subsequent runs read them as the authoritative source. If the API diverges, drift events are logged to `data/discovery.jsonl`.

`TABLE_KEY_MAP` in `endpoints.py` covers tables without a natural primary key — singleton tables (inject `id = 1`), single-column unique keys, and composite unique indexes.

### Log files

| File | Format | Contents |
|---|---|---|
| `data/pipeline_runs.jsonl` | Append-only JSONL | Every task execution: started/success/failed with duration |
| `data/pipeline_progress.json` | Atomic JSON (write-then-rename) | Current run snapshot: per-task status and loop progress |
| `data/discovery.jsonl` | Append-only JSONL | Schema drift: new/missing columns, type changes, table creation |

### Tests

83 tests in 4 files with 100% pass rate. `conftest.py` isolates tests from the real filesystem by redirecting `SCHEMA_DIR` to a temp directory via `pytest_configure`.
