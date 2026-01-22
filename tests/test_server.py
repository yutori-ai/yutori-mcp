"""Tests for server helper functions."""

from yutori_mcp.server import _output_fields_to_task_spec, _simplify_schema, _get_simplified_schema
from yutori_mcp.schemas import ListScoutsInput, CreateScoutInput


class TestSimplifySchema:
    def test_flattens_anyof_with_null(self):
        """anyOf with null type is flattened to just the non-null type."""
        schema = {
            "properties": {
                "limit": {
                    "anyOf": [
                        {"type": "integer", "minimum": 1, "maximum": 100},
                        {"type": "null"},
                    ],
                    "default": 10,
                    "description": "Max results",
                }
            }
        }
        result = _simplify_schema(schema)
        assert result["properties"]["limit"] == {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 10,
            "description": "Max results",
        }

    def test_flattens_anyof_with_enum(self):
        """anyOf with enum and null is flattened correctly."""
        schema = {
            "properties": {
                "status": {
                    "anyOf": [
                        {"type": "string", "enum": ["active", "paused", "done"]},
                        {"type": "null"},
                    ],
                    "default": None,
                }
            }
        }
        result = _simplify_schema(schema)
        assert result["properties"]["status"] == {
            "type": "string",
            "enum": ["active", "paused", "done"],
            "default": None,
        }

    def test_preserves_non_anyof_properties(self):
        """Properties without anyOf are preserved as-is."""
        schema = {
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        }
        result = _simplify_schema(schema)
        assert result == schema

    def test_handles_nested_objects(self):
        """Recursively simplifies nested objects."""
        schema = {
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "timeout": {
                            "anyOf": [{"type": "integer"}, {"type": "null"}],
                            "default": 30,
                        }
                    },
                }
            }
        }
        result = _simplify_schema(schema)
        assert result["properties"]["config"]["properties"]["timeout"] == {
            "type": "integer",
            "default": 30,
        }

    def test_handles_arrays(self):
        """Handles arrays of schemas."""
        schema = {
            "items": [
                {"anyOf": [{"type": "string"}, {"type": "null"}]},
                {"type": "integer"},
            ]
        }
        result = _simplify_schema(schema)
        assert result["items"][0] == {"type": "string"}
        assert result["items"][1] == {"type": "integer"}

    def test_preserves_anyof_without_null(self):
        """anyOf without null type is preserved."""
        schema = {
            "properties": {
                "value": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}],
                }
            }
        }
        result = _simplify_schema(schema)
        # Should be unchanged since it's not the [type, null] pattern
        assert result["properties"]["value"]["anyOf"] == [
            {"type": "string"},
            {"type": "integer"},
        ]


class TestGetSimplifiedSchema:
    def test_list_scouts_schema_has_integer_limit(self):
        """ListScoutsInput.limit should be integer, not anyOf."""
        schema = _get_simplified_schema(ListScoutsInput)
        limit_schema = schema["properties"]["limit"]
        assert limit_schema["type"] == "integer"
        assert "anyOf" not in limit_schema

    def test_list_scouts_schema_has_string_status(self):
        """ListScoutsInput.status should be string with enum, not anyOf."""
        schema = _get_simplified_schema(ListScoutsInput)
        status_schema = schema["properties"]["status"]
        assert status_schema["type"] == "string"
        assert status_schema["enum"] == ["active", "paused", "done"]
        assert "anyOf" not in status_schema

    def test_create_scout_schema_has_integer_output_interval(self):
        """CreateScoutInput.output_interval should be integer, not anyOf."""
        schema = _get_simplified_schema(CreateScoutInput)
        interval_schema = schema["properties"]["output_interval"]
        assert interval_schema["type"] == "integer"
        assert interval_schema["minimum"] == 1800
        assert "anyOf" not in interval_schema


class TestOutputFieldsToTaskSpec:
    def test_none_returns_none(self):
        """None input returns None."""
        assert _output_fields_to_task_spec(None) is None

    def test_empty_list(self):
        """Empty list produces empty properties."""
        result = _output_fields_to_task_spec([])
        assert result == {
            "output_schema": {
                "type": "json",
                "json_schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
        }

    def test_single_field(self):
        """Single field is converted correctly."""
        result = _output_fields_to_task_spec(["headline"])
        assert result == {
            "output_schema": {
                "type": "json",
                "json_schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "headline": {"type": "string"},
                        },
                    },
                },
            },
        }

    def test_multiple_fields(self):
        """Multiple fields are all converted to string properties."""
        result = _output_fields_to_task_spec(["headline", "summary", "url"])
        expected_properties = {
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "url": {"type": "string"},
        }
        assert result["output_schema"]["json_schema"]["items"]["properties"] == expected_properties

    def test_output_is_array_type(self):
        """Output schema is always array type."""
        result = _output_fields_to_task_spec(["field1"])
        assert result["output_schema"]["json_schema"]["type"] == "array"

    def test_items_are_objects(self):
        """Array items are always objects."""
        result = _output_fields_to_task_spec(["field1"])
        assert result["output_schema"]["json_schema"]["items"]["type"] == "object"
