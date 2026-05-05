# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```
uv sync                     # Install dependencies
uv run pytest               # Run all tests
uv run pytest -k "test_name"  # Run a single test
uv run python infer_endpoint_schema.py  # Print CREATE TABLE statements from live API
uv run python ingest.py     # Ingest bootstrap-static into footballdb_dev.duckdb
uv run python ingest.py --live  # Ingest into footballdb.duckdb (with backup)
```

## Configuration

Set `DB_NAME` and `DATA_DIR` in `.env` to control the database filename and directory (defaults: `footballdb`, `data`). Dev appends `_dev` to the name, live omits it.
`.env.example` mirrors `.env` without secrets — keep both in sync when adding vars.

## Architecture

Ingests FPL data from undocumented public endpoints (`https://fantasy.premierleague.com/api/`) into a DuckDB database of player and team data. See `API_ENDPOINTS.md` for the full endpoint catalog. The immediate goal is building out the database; the eventual goal is using it with dbt models to help pick teams.

- **`api_client.py`** — Thin wrapper around `requests`. Provides `get`/`post`/`put`/`delete` methods. `put` and `delete` are currently stubs.
- **`database_manager.py`** — `DatabaseManager(db_path)` opens a DuckDB connection (defaults to `footballdb_dev.duckdb`). `create_table()` generates valid `CREATE TABLE` statements; pass `execute=True` to also run them. `insert_rows()` inserts a list of dicts with parameterized queries, serializing nested values to JSON. `drop_table()` drops if exists. `backup_db()` creates timestamped `.bak` copies. `map_to_duckdb_types()` bridges Python type names to DuckDB types (`TEXT`, `INTEGER`, `DOUBLE`, `BOOLEAN`, `JSON`, `TIMESTAMP`) and auto-marks `id` columns as primary keys.
- **`infer_endpoint_schema.py`** — Takes a JSON API response dict and produces table schemas. `infer_response_schema()` walks top-level keys, sampling list items and flattening nested dicts into columns. `infer_record_schema()` extracts column types from record arrays, with None-field scanning. `_python_type_name()` maps Python values to type names (detects ISO 8601 datetimes, checks `bool` before `int`). Run directly to inspect schemas without ingesting.
- **`ingest.py`** — Orchestrates fetching, schema inference, table creation, and data insertion. Targets `footballdb_dev.duckdb` by default; `--live` flag targets `footballdb.duckdb` with automatic backup.
