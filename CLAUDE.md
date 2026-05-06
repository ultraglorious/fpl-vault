# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```
uv sync                     # Install dependencies
uv run pytest               # Run all tests
uv run pytest -k "test_name"  # Run a single test
uv run python infer_endpoint_schema.py  # Print CREATE TABLE statements from live API
uv run python pipeline.py --init          # Infer schemas + create empty tables (dev DB)
uv run python pipeline.py --init --live   # Same against production DB
uv run python pipeline.py                 # Ingest all endpoints into dev DB
uv run python pipeline.py --live          # Ingest all endpoints into production DB (with backup)
uv run python pipeline.py --tasks fixtures,event-status  # Run specific tasks only
```

## Configuration

Set `DB_NAME` and `DATA_DIR` in `.env` to control the database filename and directory (defaults: `footballdb`, `data`). Dev appends `_dev` to the name, live omits it.
`REQUEST_DELAY` controls the pause (seconds) between API calls in parameterized loops (default: 0.5).
`.env.example` mirrors `.env` without secrets — keep both in sync when adding vars.

## Architecture

Ingests FPL data from undocumented public endpoints (`https://fantasy.premierleague.com/api/`) into a DuckDB database of player and team data. See `API_ENDPOINTS.md` for the full endpoint catalog.

Schema creation and data ingestion are separate steps. Run `--init` first to create empty tables from inferred schemas, then run normally to ingest data. Normal runs will fail if tables don't exist.

- **`pipeline.py`** — Lightweight DAG orchestrator. `Pipeline` class runs tasks in dependency order (topological sort via Kahn's algorithm). `Task` is a named callable `(db) -> None`. Logs run state to `data/pipeline_runs.jsonl`. Two modes: `--init` (schema + table creation) and normal (data ingestion).
- **`endpoints.py`** — Ingestion functions for all endpoints. `init_*` functions handle schema inference and table creation. `ingest_*` functions handle data upsert. Parameterized endpoints loop over IDs from existing tables and inject the parameter as a column (e.g. `event_id`).
- **`api_client.py`** — Thin wrapper around `requests` with session reuse, 3-retry exponential backoff, and 30s timeout.
- **`database_manager.py`** — `DatabaseManager(db_path)` opens a DuckDB connection. `create_table()` generates valid `CREATE TABLE` statements; pass `execute=True` to also run them. `upsert_rows()` upserts with `ON CONFLICT DO UPDATE`. `fetch_column()` reads a column from a table. `table_exists()` checks if a table exists. `backup_db()` creates timestamped `.bak` copies.
- **`infer_endpoint_schema.py`** — Takes a JSON API response dict and produces table schemas. `infer_response_schema()` walks top-level keys with optional `table_prefix` for nested endpoints. `infer_record_schema()` extracts column types from record arrays.
- **`ingest.py`** — Legacy single-endpoint script for `bootstrap-static`. Still works standalone but bootstrap-static is also available as a pipeline task.
