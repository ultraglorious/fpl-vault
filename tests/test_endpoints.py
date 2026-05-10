import json
import os

import pytest
from database_manager import DatabaseManager, PYTHON_TO_DUCKDB
from endpoints import (
    _apply_key_map_modifiers,
    _detect_pk_columns,
    _ensure_unique_index,
    _ingest_rows,
    _init_tables,
    _json_columns,
    _normalize_response,
    _resolve_schema,
    _response_key,
    _validate_and_log_schema,
    TABLE_KEY_MAP,
)


class TestDetectPkColumns:
    """Primary key detection from schema or TABLE_KEY_MAP fallback."""

    def test_explicit_primary_key(self):
        schema = {
            "columns": [
                {"name": "id", "data_type": "INTEGER", "primary_key": True},
                {"name": "name", "data_type": "TEXT"},
            ],
        }
        assert _detect_pk_columns(schema, "test") == ["id"]

    def test_unique_on_fallback(self):
        schema = {"columns": [{"name": "name", "data_type": "TEXT"}]}
        assert _detect_pk_columns(schema, "element_stats") == ["name"]

    def test_no_pk_raises(self):
        schema = {"columns": [{"name": "val", "data_type": "INTEGER"}]}
        with pytest.raises(ValueError, match="No primary key"):
            _detect_pk_columns(schema, "unknown_table")


class TestJsonColumns:
    """Identifying columns with JSON data type."""

    def test_finds_json_columns(self):
        schema = {
            "columns": [
                {"name": "id", "data_type": "INTEGER"},
                {"name": "metadata", "data_type": "JSON"},
                {"name": "overrides", "data_type": "JSON"},
                {"name": "name", "data_type": "TEXT"},
            ],
        }
        jc = _json_columns(schema)
        assert jc == {"metadata", "overrides"}

    def test_no_json_columns(self):
        schema = {"columns": [{"name": "id", "data_type": "INTEGER"}]}
        assert _json_columns(schema) == set()


class TestResolveSchema:
    """YAML vs inferred schema resolution and drift detection."""

    def test_falls_back_to_inferred_when_no_yaml(self):
        inferred = {
            "table_name": "new_table",
            "columns": [
                {"name": "id", "type": "int", "nullable": False},
                {"name": "name", "type": "str", "nullable": True},
            ],
        }
        result = _resolve_schema("new_table", inferred, {}, "test-endpoint")
        assert result["table_name"] == "new_table"
        cols = {c["name"]: c for c in result["columns"]}
        assert cols["id"]["data_type"] == "INTEGER"
        assert cols["id"]["primary_key"] is True
        assert cols["name"]["data_type"] == "TEXT"

    def test_uses_yaml_when_available(self):
        inferred = {
            "table_name": "teams",
            "columns": [{"name": "id", "type": "int", "nullable": False}],
        }
        yaml_schemas = {
            "teams": {
                "table_name": "teams",
                "primary_key": "id",
                "columns": [
                    {"name": "id", "data_type": "INTEGER", "primary_key": True, "nullable": False},
                    {"name": "name", "data_type": "TEXT", "nullable": False},
                ],
            },
        }
        result = _resolve_schema("teams", inferred, yaml_schemas, "bootstrap-static")
        assert result is yaml_schemas["teams"]


class TestInitTables:
    """Schema init: creating empty tables from inferred schemas."""

    def test_creates_table_when_not_exists(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.conn.execute("CREATE SCHEMA IF NOT EXISTS fpl_api")

        tables = [
            {
                "table_name": "fixtures",
                "columns": [
                    {"name": "id", "type": "int", "nullable": False},
                    {"name": "name", "type": "str", "nullable": True},
                ],
            }
        ]
        _init_tables(tables, db, "fixtures")
        assert db.table_exists("fixtures")
        db.close()

    def test_skips_existing_table(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.conn.execute("CREATE SCHEMA IF NOT EXISTS fpl_api")
        db.conn.execute("CREATE TABLE fpl_api.fixtures (id INTEGER PRIMARY KEY)")

        tables = [
            {
                "table_name": "fixtures",
                "columns": [{"name": "id", "type": "int", "nullable": False}],
            }
        ]
        _init_tables(tables, db, "fixtures")
        db.close()


class TestIngestRows:
    """Data ingestion: upserting API responses into existing tables."""

    def test_upserts_into_existing_table(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.conn.execute("CREATE SCHEMA IF NOT EXISTS fpl_api")
        db.conn.execute("CREATE TABLE fpl_api.fixtures (id INTEGER PRIMARY KEY, name TEXT)")

        tables = [
            {
                "table_name": "fixtures",
                "columns": [
                    {"name": "id", "type": "int", "nullable": False},
                    {"name": "name", "type": "str", "nullable": True},
                ],
            }
        ]
        response = {"fixtures": [{"id": 1, "name": "ARS vs CHE"}]}
        _ingest_rows(tables, response, db, "fixtures")

        result = db.conn.execute("SELECT COUNT(*) FROM fpl_api.fixtures").fetchone()
        assert result[0] == 1
        db.close()

    def test_raises_when_table_missing(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.conn.execute("CREATE SCHEMA IF NOT EXISTS fpl_api")

        tables = [
            {
                "table_name": "nonexistent",
                "columns": [{"name": "id", "type": "int", "nullable": False}],
            }
        ]
        with pytest.raises(RuntimeError, match="does not exist"):
            _ingest_rows(tables, {"nonexistent": []}, db, "test-endpoint")
        db.close()

    def test_wraps_dict_row_in_list(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.conn.execute("CREATE SCHEMA IF NOT EXISTS fpl_api")
        db.conn.execute("CREATE TABLE fpl_api.game_settings (id INTEGER PRIMARY KEY, setting TEXT)")

        saved = TABLE_KEY_MAP.get("game_settings")
        TABLE_KEY_MAP["game_settings"] = {"type": "singleton"}
        try:
            tables = [
                {
                    "table_name": "game_settings",
                    "columns": [{"name": "setting", "type": "str", "nullable": True}],
                }
            ]

            response = {"game_settings": {"setting": "value"}}
            _ingest_rows(tables, response, db, "bootstrap-static")

            result = db.conn.execute("SELECT id, setting FROM fpl_api.game_settings").fetchone()
            assert result[0] == 1
            assert result[1] == "value"
        finally:
            if saved is None:
                del TABLE_KEY_MAP["game_settings"]
            else:
                TABLE_KEY_MAP["game_settings"] = saved
            db.close()

    def test_injects_extra_columns(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.conn.execute("CREATE SCHEMA IF NOT EXISTS fpl_api")
        db.conn.execute("CREATE TABLE fpl_api.data (id INTEGER PRIMARY KEY, name TEXT, event_id INTEGER)")

        tables = [
            {
                "table_name": "data",
                "columns": [
                    {"name": "id", "type": "int", "nullable": False},
                    {"name": "name", "type": "str", "nullable": True},
                ],
            }
        ]
        response = {"data": [{"id": 1, "name": "test"}]}
        _ingest_rows(tables, response, db, "event-live", extra_columns={"event_id": 5})

        result = db.conn.execute("SELECT event_id FROM fpl_api.data WHERE id = 1").fetchone()
        assert result[0] == 5
        db.close()


class TestValidateAndLogSchema:
    """Schema drift detection: logging new columns from the API."""

    def test_logs_new_columns_not_in_yaml(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        inferred = {
            "columns": [
                {"name": "id", "type": "int"},
                {"name": "new_field", "type": "str"},
            ]
        }
        yaml_schema = {
            "columns": [
                {"name": "id", "data_type": "INTEGER"},
            ]
        }
        _validate_and_log_schema("test_table", inferred, yaml_schema, "test-endpoint")
        log_path = tmp_path / "discovery.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["event"] == "new_column"
        assert entry["column"] == "new_field"

    def test_no_log_when_no_new_columns(self, tmp_path):
        os.environ["DATA_DIR"] = str(tmp_path)
        inferred = {
            "columns": [{"name": "id", "type": "int"}]
        }
        yaml_schema = {
            "columns": [{"name": "id", "data_type": "INTEGER"}]
        }
        _validate_and_log_schema("test_table", inferred, yaml_schema, "test-endpoint")
        log_path = tmp_path / "discovery.jsonl"
        assert not log_path.exists()


class TestResponseKey:
    """Reversing table_prefix to get the original API response key."""

    def test_no_prefix_returns_original(self):
        assert _response_key("fixtures") == "fixtures"

    def test_strips_prefix(self):
        assert _response_key("element_summary_fixtures", "element_summary") == "fixtures"

    def test_empty_prefix_returns_original(self):
        assert _response_key("teams", "") == "teams"


class TestNormalizeResponse:
    """Wrapping bare-list API responses into keyed dicts."""

    def test_passes_through_dict(self):
        data = {"key": "value"}
        assert _normalize_response(data, "events/") is data

    def test_wraps_bare_list_using_last_path_segment(self):
        data = [{"id": 1}, {"id": 2}]
        result = _normalize_response(data, "fixtures/")
        assert result == {"fixtures": data}

    def test_wraps_bare_list_without_trailing_slash(self):
        data = [{"id": 1}]
        result = _normalize_response(data, "event-status")
        assert result == {"event-status": data}


class TestApplyKeyMapModifiers:
    """TABLE_KEY_MAP modifiers: singleton id injection and unique constraints."""

    def test_singleton_injects_id_and_wraps_row(self):
        mapped = {
            "table_name": "game_settings",
            "columns": [{"name": "setting", "data_type": "TEXT"}],
        }
        rows = [{"setting": "value"}]
        result = _apply_key_map_modifiers(mapped, "game_settings", rows)
        assert mapped["columns"][0]["name"] == "id"
        assert mapped["columns"][0]["primary_key"] is True
        assert result[0]["id"] == 1
        assert result[0]["setting"] == "value"

    def test_singleton_no_duplicate_id_column(self):
        mapped = {
            "table_name": "game_settings",
            "columns": [{"name": "id", "data_type": "INTEGER", "primary_key": True}],
        }
        rows = [{"id": 99, "setting": "value"}]
        result = _apply_key_map_modifiers(mapped, "game_settings", rows)
        assert len(mapped["columns"]) == 1
        assert result[0]["id"] == 1

    def test_singleton_with_no_rows_returns_none(self):
        mapped = {
            "table_name": "game_settings",
            "columns": [{"name": "setting", "data_type": "TEXT"}],
        }
        result = _apply_key_map_modifiers(mapped, "game_settings", None)
        assert result is None
        assert mapped["columns"][0]["name"] == "id"

    def test_unique_on_single_column_adds_unique(self):
        mapped = {
            "table_name": "element_stats",
            "columns": [{"name": "name", "data_type": "TEXT"}],
        }
        _apply_key_map_modifiers(mapped, "element_stats")
        assert mapped["columns"][0]["unique"] is True

    def test_no_key_map_returns_unaltered(self):
        mapped = {
            "table_name": "teams",
            "columns": [{"name": "id", "data_type": "INTEGER", "primary_key": True}],
        }
        rows = [{"id": 1}]
        result = _apply_key_map_modifiers(mapped, "teams", rows)
        assert result is rows


class TestEnsureUniqueIndex:
    """Composite UNIQUE INDEX creation for multi-column key tables."""

    def test_creates_composite_unique_index(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.conn.execute("CREATE SCHEMA IF NOT EXISTS fpl_api")
        db.conn.execute(
            "CREATE TABLE fpl_api.dream_team_team ("
            "event_id INTEGER, element INTEGER, points INTEGER"
            ")"
        )
        _ensure_unique_index(db, "dream_team_team")
        db.close()

    def test_skips_single_column_unique_on(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.conn.execute("CREATE SCHEMA IF NOT EXISTS fpl_api")
        db.conn.execute("CREATE TABLE fpl_api.element_stats (name TEXT)")
        _ensure_unique_index(db, "element_stats")
        db.close()
