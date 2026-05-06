import pytest
from database_manager import DatabaseManager, PYTHON_TO_DUCKDB
from endpoints import (
    _detect_pk_columns,
    _json_columns,
    _resolve_schema,
    _init_tables,
    _ingest_rows,
    TABLE_KEY_MAP,
)


class TestDetectPkColumns:
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

        TABLE_KEY_MAP["game_settings"] = {"type": "singleton"}

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
