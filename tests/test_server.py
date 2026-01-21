"""Tests for server helper functions."""

from yutori_mcp.server import _output_fields_to_task_spec


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
