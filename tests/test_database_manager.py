from database_manager import DatabaseManager, map_to_duckdb_types


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

    def test_execute_query_works(self):
        db = DatabaseManager(":memory:")
        db.execute_query("CREATE TABLE t (id INTEGER)")
        result = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert ("t",) in result
        db.close()
