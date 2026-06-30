import json
import logging

from server_modules.logging_config import _JsonLogFormatter


def test_json_log_formatter_emits_machine_readable_payload():
    formatter = _JsonLogFormatter()
    record = logging.LogRecord(
        name="empyralis.runtime",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="runtime ready",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-123"
    record.workspace_id = "default"

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "empyralis.runtime"
    assert payload["message"] == "runtime ready"
    assert payload["request_id"] == "req-123"
    assert payload["workspace_id"] == "default"
    assert "timestamp" in payload
