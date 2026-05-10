from database_manager import DatabaseManager, load_table_schemas, map_to_duckdb_types


class TestMapToDuckDBTypes:
    def test_type_conversion(self):
        schema = {
            "table_name": "players",
            "columns": [
                {"name": "id", "type": "int", "nullable": False},
                {"name": "name", "type": "str", "nullable": False},
                {"name": "score", "type": "float", "nullable": True},
                {"name": "active", "type": "bool", "nullable": False},
                {"name": "metadata", "type": "jsonb", "nullable": True},
                {"name": "created_at", "type": "datetime", "nullable": False},
            ],
        }
        result = map_to_duckdb_types(schema)
        assert result["table_name"] == "players"
        cols = {c["name"]: c for c in result["columns"]}
        assert cols["id"]["data_type"] == "INTEGER"
        assert cols["name"]["data_type"] == "TEXT"
        assert cols["score"]["data_type"] == "DOUBLE"
        assert cols["active"]["data_type"] == "BOOLEAN"
        assert cols["metadata"]["data_type"] == "JSON"
        assert cols["created_at"]["data_type"] == "TIMESTAMP"

    def test_id_int_becomes_primary_key(self):
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "id", "type": "int", "nullable": False},
                {"name": "name", "type": "str", "nullable": False},
            ],
        }
        result = map_to_duckdb_types(schema)
        id_col = next(c for c in result["columns"] if c["name"] == "id")
        assert id_col["primary_key"] is True
        assert id_col["data_type"] == "INTEGER"

    def test_non_id_int_not_primary_key(self):
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "id", "type": "int", "nullable": False},
                {"name": "team_id", "type": "int", "nullable": False},
            ],
        }
        result = map_to_duckdb_types(schema)
        team_col = next(c for c in result["columns"] if c["name"] == "team_id")
        assert "primary_key" not in team_col

    def test_preserves_foreign_keys(self):
        schema = {
            "table_name": "t",
            "columns": [{"name": "id", "type": "int", "nullable": False}],
            "foreign_keys": [
                {"column_name": "team_id", "referenced_table": "teams", "referenced_column": "id"}
            ],
        }
        result = map_to_duckdb_types(schema)
        assert "foreign_keys" in result
        assert result["foreign_keys"] == schema["foreign_keys"]

    def test_unknown_type_defaults_to_text(self):
        schema = {
            "table_name": "t",
            "columns": [{"name": "weird", "type": "unknown_x", "nullable": True}],
        }
        result = map_to_duckdb_types(schema)
        assert result["columns"][0]["data_type"] == "TEXT"


class TestCreateTable:
    def test_simple_table(self):
        db = DatabaseManager(":memory:")
        schema = {
            "table_name": "players",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "primary_key": True},
                {"name": "name", "data_type": "TEXT", "nullable": False},
            ],
        }
        db.create_table(schema)
        db.close()

    def test_has_comma_separators(self):
        db = DatabaseManager(":memory:")
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "a", "data_type": "INTEGER"},
                {"name": "b", "data_type": "TEXT"},
                {"name": "c", "data_type": "BOOLEAN"},
            ],
        }
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        db.create_table(schema)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert ",\n" in output
        db.close()

    def test_primary_key_inline(self):
        db = DatabaseManager(":memory:")
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "primary_key": True},
            ],
        }
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        db.create_table(schema)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "PRIMARY KEY" in output
        db.close()

    def test_foreign_key_syntax(self):
        db = DatabaseManager(":memory:")
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "primary_key": True},
                {"name": "team_id", "data_type": "INTEGER"},
            ],
            "foreign_keys": [
                {"column_name": "team_id", "referenced_table": "teams", "referenced_column": "id"}
            ],
        }
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        db.create_table(schema)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "FOREIGN KEY (team_id) REFERENCES teams(id)" in output
        db.close()

    def test_not_null_in_output(self):
        db = DatabaseManager(":memory:")
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "name", "data_type": "TEXT", "nullable": False},
            ],
        }
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        db.create_table(schema)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "NOT NULL" in output
        db.close()

    def test_creates_valid_sql_with_semicolon(self):
        db = DatabaseManager(":memory:")
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "primary_key": True},
            ],
        }
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        db.create_table(schema)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert output.strip().endswith(";")
        db.close()

    def test_unique_constraint(self):
        db = DatabaseManager(":memory:")
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "name", "data_type": "TEXT", "unique": True},
            ],
        }
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        db.create_table(schema)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "UNIQUE" in output
        db.close()

    def test_if_not_exists_default(self):
        db = DatabaseManager(":memory:")
        schema = {
            "table_name": "t",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "primary_key": True},
            ],
        }
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        db.create_table(schema)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "IF NOT EXISTS" in output
        db.close()

    def test_execute_query_works(self):
        db = DatabaseManager(":memory:")
        db.execute_query("CREATE TABLE t (id INTEGER)")
        result = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert ("t",) in result
        db.close()

    def test_schema_qualified_table_name(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        schema = {
            "table_name": "players",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "primary_key": True},
            ],
        }
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        db.create_table(schema)
        sys.stdout = sys.__stdout__
        output = captured.getvalue()
        assert "fpl_api.players" in output
        assert "IF NOT EXISTS" in output
        db.close()


class TestUpsertRows:
    def test_inserts_into_empty_table(self):
        db = DatabaseManager(":memory:")
        db.execute_query("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        db.upsert_rows("t", [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], pk_columns=["id"])
        result = db.conn.execute("SELECT COUNT(*) FROM t").fetchone()
        assert result[0] == 2
        db.close()

    def test_updates_existing_rows(self):
        db = DatabaseManager(":memory:")
        db.execute_query("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        db.upsert_rows("t", [{"id": 1, "name": "old"}], pk_columns=["id"])
        db.upsert_rows("t", [{"id": 1, "name": "new"}], pk_columns=["id"])
        result = db.conn.execute("SELECT id, name FROM t WHERE id = 1").fetchone()
        assert result[1] == "new"
        count = db.conn.execute("SELECT COUNT(*) FROM t").fetchone()
        assert count[0] == 1
        db.close()

    def test_mixed_insert_and_update(self):
        db = DatabaseManager(":memory:")
        db.execute_query("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        db.upsert_rows("t", [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], pk_columns=["id"])
        db.upsert_rows("t", [{"id": 1, "name": "updated"}, {"id": 3, "name": "c"}], pk_columns=["id"])
        count = db.conn.execute("SELECT COUNT(*) FROM t").fetchone()
        assert count[0] == 3
        name = db.conn.execute("SELECT name FROM t WHERE id = 1").fetchone()
        assert name[0] == "updated"
        db.close()

    def test_handles_json_columns(self):
        db = DatabaseManager(":memory:")
        db.execute_query("CREATE TABLE t (id INTEGER PRIMARY KEY, data JSON)")
        db.upsert_rows("t", [{"id": 1, "data": {"key": "value"}}], pk_columns=["id"], json_columns={"data"})
        result = db.conn.execute("SELECT data FROM t WHERE id = 1").fetchone()
        assert result[0] == '{"key": "value"}'
        db.close()

    def test_upsert_with_schema(self):
        db = DatabaseManager(":memory:", schema="fpl_api")
        db.execute_query("CREATE SCHEMA IF NOT EXISTS fpl_api")
        db.execute_query("CREATE TABLE fpl_api.t (id INTEGER PRIMARY KEY, name TEXT)")
        db.upsert_rows("t", [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], pk_columns=["id"])
        result = db.conn.execute("SELECT COUNT(*) FROM fpl_api.t").fetchone()
        assert result[0] == 2
        db.close()


class TestLoadTableSchemas:
    def test_loads_tables_from_yaml(self, tmp_path):
        yml = tmp_path / "teams.yml"
        yml.write_text("""
name: teams
primary_key: id
columns:
  - name: id
    data_type: INTEGER
    tests: [unique, not_null]
  - name: name
    data_type: TEXT
    tests: [not_null]
  - name: form
    data_type: TEXT
""")
        schemas = load_table_schemas(str(tmp_path))
        assert "teams" in schemas
        teams = schemas["teams"]
        assert teams["table_name"] == "teams"
        assert teams["primary_key"] == "id"
        cols = {c["name"]: c for c in teams["columns"]}
        assert cols["id"]["data_type"] == "INTEGER"
        assert cols["id"]["primary_key"] is True
        assert cols["id"]["nullable"] is False
        assert cols["name"]["data_type"] == "TEXT"
        assert cols["name"]["nullable"] is False
        assert cols["form"]["data_type"] == "TEXT"
        assert cols["form"]["nullable"] is True

    def test_handles_singleton_primary_key(self, tmp_path):
        yml = tmp_path / "game_settings.yml"
        yml.write_text("""
name: game_settings
primary_key: "!singleton"
columns:
  - name: league_max_team
    data_type: INTEGER
    tests: [not_null]
""")
        schemas = load_table_schemas(str(tmp_path))
        assert "game_settings" in schemas
        gs = schemas["game_settings"]
        assert gs["primary_key"] == "!singleton"
        assert gs["columns"][0].get("primary_key") is not True

    def test_empty_directory_returns_empty_dict(self, tmp_path):
        schemas = load_table_schemas(str(tmp_path))
        assert schemas == {}

    def test_multiple_files(self, tmp_path):
        (tmp_path / "teams.yml").write_text("name: teams\ncolumns:\n  - name: id\n    data_type: INTEGER\n")
        (tmp_path / "events.yml").write_text("name: events\ncolumns:\n  - name: id\n    data_type: INTEGER\n")
        schemas = load_table_schemas(str(tmp_path))
        assert set(schemas.keys()) == {"teams", "events"}


class TestSaveTableSchema:
    def test_writes_and_roundtrips(self, tmp_path):
        mapped = {
            "table_name": "teams",
            "primary_key": "id",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "nullable": False, "primary_key": True},
                {"name": "name", "data_type": "TEXT", "nullable": False},
                {"name": "form", "data_type": "TEXT", "nullable": True},
            ],
        }
        from database_manager import save_table_schema
        save_table_schema(str(tmp_path), mapped)

        yml_file = tmp_path / "teams.yml"
        assert yml_file.exists()

        schemas = load_table_schemas(str(tmp_path))
        assert "teams" in schemas
        teams = schemas["teams"]
        assert teams["table_name"] == "teams"
        assert teams["primary_key"] == "id"
        cols = {c["name"]: c for c in teams["columns"]}
        assert cols["id"]["nullable"] is False
        assert cols["id"]["primary_key"] is True
        assert cols["name"]["nullable"] is False
        assert cols["form"]["nullable"] is True

    def test_creates_directory(self, tmp_path):
        schema_dir = tmp_path / "new_schema"
        mapped = {"table_name": "t", "columns": [{"name": "id", "data_type": "INTEGER", "nullable": False}]}
        from database_manager import save_table_schema
        save_table_schema(str(schema_dir), mapped)
        assert (schema_dir / "t.yml").exists()
