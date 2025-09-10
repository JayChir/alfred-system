"""
Tests for SSE utility functions.

Tests cover:
- SSE wire format helper
- Heartbeat generation
- Tool data truncation
- Sensitive field redaction
"""


from src.utils.sse_utils import (
    redact_sensitive_fields,
    sse_format,
    sse_heartbeat,
    sse_retry,
    truncate_tool_data,
)


def test_sse_format_basic():
    """Test basic SSE formatting."""
    # Simple data with event
    result = sse_format({"message": "hello"}, event="test")
    assert result == 'event: test\ndata: {"message":"hello"}\n\n'

    # String data
    result = sse_format("simple string", event="ping")
    assert result == "event: ping\ndata: simple string\n\n"

    # Data without event
    result = sse_format({"value": 42})
    assert result == 'data: {"value":42}\n\n'


def test_sse_format_with_id():
    """Test SSE formatting with ID field."""
    result = sse_format({"content": "test"}, event="message", id="msg-123")
    assert "event: message\n" in result
    assert "id: msg-123\n" in result
    assert 'data: {"content":"test"}\n' in result
    assert result.endswith("\n\n")


def test_sse_format_with_retry():
    """Test SSE formatting with retry directive."""
    result = sse_format({"status": "connected"}, event="init", retry=5000)
    assert "event: init\n" in result
    assert "retry: 5000\n" in result
    assert 'data: {"status":"connected"}\n' in result


def test_sse_heartbeat():
    """Test heartbeat comment generation."""
    result = sse_heartbeat()
    assert result == ": hb\n\n"


def test_sse_retry():
    """Test retry directive generation."""
    result = sse_retry(3000)
    assert result == "retry: 3000\n\n"

    # Default value
    result = sse_retry()
    assert result == "retry: 5000\n\n"


def test_truncate_tool_data():
    """Test tool data truncation."""
    # Large string result
    data = {
        "tool": "search",
        "result": "x" * 2000,
        "args": {"query": "test"},
    }

    truncated = truncate_tool_data(data, max_length=100)

    assert truncated["tool"] == "search"
    assert len(truncated["result"]) == 103  # 100 + "..."
    assert truncated["result"].endswith("...")
    assert truncated["result_truncated"] is True
    assert truncated["result_original_length"] == 2000
    assert truncated["args"] == {"query": "test"}  # Unchanged


def test_truncate_tool_data_nested():
    """Test truncation of nested structures."""
    data = {
        "tool": "fetch",
        "output": {"nested": {"data": "y" * 3000}},
        "metadata": "small",
    }

    truncated = truncate_tool_data(data, max_length=500)

    # Output should be truncated (converted to string first)
    assert truncated["output_truncated"] is True
    assert isinstance(truncated["output"], str)  # Converted to string
    assert truncated["output"].endswith("...")  # Truncated
    assert len(truncated["output"]) == 503  # 500 + "..."
    assert truncated["metadata"] == "small"  # Unchanged


def test_truncate_tool_data_custom_fields():
    """Test truncation with custom field list."""
    data = {
        "tool": "custom",
        "big_field": "z" * 1000,
        "result": "normal",
    }

    # Only truncate big_field
    truncated = truncate_tool_data(
        data, max_length=50, fields_to_truncate=["big_field"]
    )

    assert len(truncated["big_field"]) == 53  # 50 + "..."
    assert truncated["result"] == "normal"  # Not truncated


def test_redact_sensitive_fields():
    """Test sensitive field redaction."""
    data = {
        "tool": "api_call",
        "api_key": "sk-12345",
        "password": "secret123",
        "result": "success",
        "metadata": {
            "token": "bearer-xyz",
            "public_data": "visible",
        },
    }

    redacted = redact_sensitive_fields(data)

    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["password"] == "***REDACTED***"
    assert redacted["result"] == "success"  # Unchanged
    assert redacted["metadata"]["token"] == "***REDACTED***"
    assert redacted["metadata"]["public_data"] == "visible"  # Unchanged


def test_redact_sensitive_fields_custom_patterns():
    """Test redaction with custom patterns."""
    data = {
        "custom_secret": "hidden",
        "api_key": "visible",  # Not in custom list
        "public": "data",
    }

    redacted = redact_sensitive_fields(data, sensitive_patterns=["custom_secret"])

    assert redacted["custom_secret"] == "***REDACTED***"
    assert redacted["api_key"] == "visible"  # Not redacted
    assert redacted["public"] == "data"


def test_redact_sensitive_fields_nested_list():
    """Test redaction in nested lists."""
    data = {
        "configs": [
            {"name": "config1", "api_key": "key1"},
            {"name": "config2", "secret": "hidden"},
        ],
        "status": "ok",
    }

    redacted = redact_sensitive_fields(data)

    assert redacted["configs"][0]["api_key"] == "***REDACTED***"
    assert redacted["configs"][1]["secret"] == "***REDACTED***"
    assert redacted["configs"][0]["name"] == "config1"
    assert redacted["status"] == "ok"


def test_combined_protection():
    """Test combining truncation and redaction."""
    data = {
        "tool": "database_query",
        "password": "secret123",  # This will be redacted
        "result": "x" * 5000,
        "query": "SELECT * FROM users",
    }

    # First redact
    protected = redact_sensitive_fields(data)
    # Then truncate
    protected = truncate_tool_data(protected, max_length=100)

    # Password should be redacted
    assert protected["password"] == "***REDACTED***"
    # Result should be truncated
    assert protected["result_truncated"] is True
    assert len(protected["result"]) == 103
