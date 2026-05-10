import pytest
from infer_endpoint_schema import (
    _python_type_name,
    infer_record_schema,
    infer_response_schema,
    UnknownDataType,
)


class TestPythonTypeName:
    """Mapping Python runtime values to type name strings."""

    def test_none_returns_none(self):
        assert _python_type_name(None) is None

    def test_bool(self):
        assert _python_type_name(True) == "bool"
        assert _python_type_name(False) == "bool"

    def test_int(self):
        assert _python_type_name(42) == "int"

    def test_float(self):
        assert _python_type_name(3.14) == "float"

    def test_str(self):
        assert _python_type_name("hello") == "str"

    def test_datetime_string(self):
        assert _python_type_name("2025-08-15T17:30:00Z") == "datetime"

    def test_datetime_string_space_separator(self):
        assert _python_type_name("2025-08-15 17:30:00") == "datetime"

    def test_dict_returns_jsonb(self):
        assert _python_type_name({"a": 1}) == "jsonb"

    def test_list_returns_jsonb(self):
        assert _python_type_name([1, 2, 3]) == "jsonb"

    def test_unknown_type_raises(self):
        with pytest.raises(UnknownDataType):
            _python_type_name(complex(1, 2))

    def test_bool_checked_before_int(self):
        assert _python_type_name(True) == "bool"


class TestInferRecordSchema:
    """Extracting column definitions from a list of record dicts."""

    def test_happy_path(self):
        records = [{"id": 1, "name": "Salah", "active": True, "score": 8.5}]
        columns = infer_record_schema(records)
        assert columns == [
            {"name": "id", "type": "int", "nullable": False},
            {"name": "name", "type": "str", "nullable": False},
            {"name": "active", "type": "bool", "nullable": False},
            {"name": "score", "type": "float", "nullable": False},
        ]

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="Empty record list"):
            infer_record_schema([])

    def test_non_dict_elements_raise(self):
        with pytest.raises(ValueError, match="List elements must be dicts"):
            infer_record_schema([1, 2, 3])

    def test_none_field_scans_ahead(self):
        records = [
            {"id": 1, "name": "Salah", "form": None},
            {"id": 2, "name": "Son", "form": "7.5"},
        ]
        columns = infer_record_schema(records)
        form_col = next(c for c in columns if c["name"] == "form")
        assert form_col["type"] == "str"
        assert form_col["nullable"] is True

    def test_none_field_all_none_defaults_to_str(self):
        records = [
            {"id": 1, "name": "Salah", "detail": None},
            {"id": 2, "name": "Son", "detail": None},
        ]
        columns = infer_record_schema(records)
        detail_col = next(c for c in columns if c["name"] == "detail")
        assert detail_col["type"] == "str"
        assert detail_col["nullable"] is True


class TestInferResponseSchema:
    """Walking API response dicts to discover table schemas."""

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError, match="API response is empty"):
            infer_response_schema({})

    def test_skips_scalars(self):
        data = {"total_players": 13000000}
        tables = infer_response_schema(data)
        assert tables == []

    def test_skips_empty_lists(self):
        data = {"empty_list": []}
        tables = infer_response_schema(data)
        assert tables == []

    def test_skips_list_of_scalars(self):
        data = {"scores": [1, 2, 3]}
        tables = infer_response_schema(data)
        assert tables == []

    def test_array_of_dicts_becomes_table(self):
        data = {"teams": [{"id": 1, "name": "Arsenal"}, {"id": 2, "name": "Chelsea"}]}
        tables = infer_response_schema(data)
        assert len(tables) == 1
        assert tables[0]["table_name"] == "teams"
        assert len(tables[0]["columns"]) == 2

    def test_nested_dict_becomes_table(self):
        data = {"game_settings": {"league_max_size": 20, "timezone": "UTC"}}
        tables = infer_response_schema(data)
        assert len(tables) == 1
        assert tables[0]["table_name"] == "game_settings"
        assert len(tables[0]["columns"]) == 2

    def test_integration_bootstrap_shape(self):
        data = {
            "events": [
                {"id": 1, "name": "Gameweek 1", "finished": True, "deadline_time": "2025-08-15T17:30:00Z"},
                {"id": 2, "name": "Gameweek 2", "finished": False, "deadline_time": "2025-08-22T17:30:00Z"},
            ],
            "teams": [
                {"id": 1, "name": "Arsenal", "code": 3},
                {"id": 2, "name": "Chelsea", "code": 4},
            ],
            "total_players": 13000000,
            "game_settings": {"league_max_size": 20, "timezone": "UTC"},
            "game_config": {"settings": {"timezone": "UTC"}, "rules": {"squad_size": 15}},
        }
        tables = infer_response_schema(data)
        table_names = {t["table_name"] for t in tables}
        assert table_names == {"events", "teams", "game_settings", "game_config"}
        # total_players (scalar) is skipped
