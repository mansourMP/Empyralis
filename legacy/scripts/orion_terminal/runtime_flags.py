from __future__ import annotations

import os


FULL_OUTPUT = os.getenv("ORION_CLI_FULL_OUTPUT", "0") == "1"
COMPACT_MODE = os.getenv("ORION_CLI_COMPACT", "1") == "1"
STREAM_PREVIEW_CHARS = max(80, int(os.getenv("ORION_CLI_STREAM_PREVIEW_CHARS", "140")))
RESULT_PREVIEW_CHARS = max(120, int(os.getenv("ORION_CLI_RESULT_PREVIEW_CHARS", "260")))
SPINNER_ENABLED = os.getenv("ORION_CLI_SPINNER", "1") != "0"
SPINNER_STYLE = os.getenv("ORION_CLI_SPINNER_STYLE", "hex").strip().lower()
try:
    _spinner_interval_raw = float(os.getenv("ORION_CLI_SPINNER_INTERVAL_SECONDS", "0.75"))
except Exception:
    _spinner_interval_raw = 0.75
SPINNER_INTERVAL_SECONDS = max(0.05, _spinner_interval_raw)
ORION_SECURITY_DOC_URL = os.getenv("ORION_SECURITY_DOC_URL", "https://docs.orion.local/security")
ORION_SETUP_DOC_URL = os.getenv("ORION_SETUP_DOC_URL", "https://docs.orion.local/setup")
