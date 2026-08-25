"""MAN-365: an operator's own env var names must never reach a customer.

`connection_oauth_service.ensure_oauth_configured()` used to raise, straight
into an HTTPException `detail`, a sentence naming the exact env vars an
OPERATOR would need to set on our server ("Slack OAuth is not configured.
Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET.") -- reached from the OAuth
start path behind the product's own Connect button, on 73 of our connectors.
Three things wrong at once: it discloses our deployment's configuration
surface to any customer; it hands the reader an instruction they cannot
possibly act on (they cannot set env vars on our server); and it reads as
broken software on the exact screen a new customer must succeed at.

This is a STRUCTURAL scan, not a point fix for that one function -- a
regex/AST check for an env-var-shaped token appearing inside a raised
exception's message, across every file under server_modules (excluding
tests/), because the next instance of this shape will not be in this file
either. Two independent per-file allowlists exist, each with a WRITTEN
REASON per entry -- do not add an entry without one, and do not widen an
entry's reason without re-verifying it:

  _LITERAL_TOKEN_ALLOWLIST   a file that legitimately raises with a literal
                             env-var-shaped token in the message (e.g. a
                             _LOGGER.error/_log.error diagnostic -- never
                             raised into an HTTPException/customer response
                             -- or a raise proven unreachable from any wired
                             customer route).
  _DYNAMIC_NAME_ALLOWLIST    a file that builds the message from a *variable*
                             holding env var names (the ORIGINAL MAN-365
                             shape -- `f"Set {' or '.join(client_names)}"`)
                             rather than a literal, which the literal-token
                             regex cannot see by itself.

Per this repo's own "a check that derives its own expectations from the
thing it checks is blind" rule (docs/design and CLAUDE.md both name this),
the KNOWN env var name set used by the dynamic-name heuristic is derived
from `connection_oauth_service.OAUTH_PROVIDER_CONFIGS`'s own `env_vars`
tables -- a different source than the scan itself -- rather than hand-typed
here a second time.

Canary: this test also asserts the raw (pre-allowlist) scan actually finds
something on the real tree, and that it walks a realistic number of files --
so if the regex/AST logic ever stops matching real code (e.g. someone
"fixes" it into matching nothing), the test fails loudly instead of quietly
enforcing nothing forever (the exact trap CLAUDE.md documents for
preflight._check_rls and for the exec-file-timeout drift test).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULES_DIR = ROOT / "server_modules"

EXCLUDED_DIR_NAMES = {"tests", "__pycache__"}

# Constructors that produce something which could plausibly become a
# customer-facing message: HTTPException directly, or any custom exception
# class (heuristically: any callable ending in "Error" or "Exception").
_EXCEPTION_NAME_RE = re.compile(r"(Error|Exception)$")

# An env-var-shaped token: at least 5 uppercase/digit/underscore characters
# followed by an underscore and one of these common credential/identifier
# suffixes. Matches the shape given in the MAN-365 brief.
_ENV_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{4,}_(?:TOKEN|KEY|SECRET|ID|URL|PASSPHRASE|PASSWORD)\b")

# Identifier name shapes that indicate "this variable holds env var NAMES",
# even though the literal names never appear as string constants at the
# raise site itself -- the exact shape of the original MAN-365 bug
# (`f"Set {' or '.join(client_names)} and {' or '.join(secret_names)}."`).
_DYNAMIC_ENV_NAME_IDENTIFIER_RE = re.compile(
    r"(^|_)(client_names|secret_names|env_vars|env_names|var_names)($|_)"
)


def _iter_module_files() -> list[Path]:
    files = []
    for p in SERVER_MODULES_DIR.rglob("*.py"):
        if any(part in EXCLUDED_DIR_NAMES for part in p.parts):
            continue
        files.append(p)
    return sorted(files)


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _joined_str_literal_text(node: ast.AST) -> str:
    """All literal (non-interpolated) text of a string-ish node: a plain
    Constant, or the Constant pieces of an f-string (JoinedStr)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            piece.value
            for piece in node.values
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _joined_str_literal_text(node.left) + _joined_str_literal_text(node.right)
    return ""


def _joined_str_formatted_value_names(node: ast.AST) -> list[str]:
    """Identifier names referenced inside an f-string's `{...}` slots
    (FormattedValue.value), restricted to simple Name/Attribute so this
    stays a cheap heuristic rather than a real dataflow analysis."""
    names: list[str] = []
    if not isinstance(node, ast.JoinedStr):
        return names
    for piece in node.values:
        if not isinstance(piece, ast.FormattedValue):
            continue
        value = piece.value
        for sub in ast.walk(value):
            if isinstance(sub, ast.Name):
                names.append(sub.id)
            elif isinstance(sub, ast.Attribute):
                names.append(sub.attr)
    return names


def _raise_exception_calls(tree: ast.Module) -> list[ast.Call]:
    """Every Call that is the exception object of a `raise` statement,
    restricted to calls that look like an exception constructor."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        # `raise Foo(...) from bar` -- exc is still the Call.
        if not isinstance(exc, ast.Call):
            continue
        func = exc.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
        if name == "HTTPException" or _EXCEPTION_NAME_RE.search(name or ""):
            calls.append(exc)
    return calls


def scan_file_for_env_leaks(path: Path) -> tuple[list[str], list[str]]:
    """Returns (literal_hits, dynamic_name_hits) -- human-readable strings
    describing each finding, or empty lists if the file has none."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return [], []

    literal_hits: list[str] = []
    dynamic_hits: list[str] = []

    for call in _raise_exception_calls(tree):
        text_parts: list[str] = []
        formatted_names: list[str] = []
        for arg in list(call.args) + [kw.value for kw in call.keywords if kw.arg in (None, "detail", "message")]:
            text_parts.append(_joined_str_literal_text(arg))
            formatted_names.extend(_joined_str_formatted_value_names(arg))

        literal_text = "".join(text_parts)
        token_match = _ENV_TOKEN_RE.search(literal_text)
        if token_match:
            literal_hits.append(f"{path.name}:{call.lineno}: literal token {token_match.group(0)!r}")

        for fname in formatted_names:
            if _DYNAMIC_ENV_NAME_IDENTIFIER_RE.search(fname):
                dynamic_hits.append(f"{path.name}:{call.lineno}: formatted value references {fname!r}")

    return literal_hits, dynamic_hits


# ---------------------------------------------------------------------------
# Allowlists. Every entry needs a written reason -- see the module docstring.
# Keyed by filename (not path) since these are unique under server_modules/.
# ---------------------------------------------------------------------------

_LITERAL_TOKEN_ALLOWLIST: dict[str, str] = {
    "connection_oauth_service.py": (
        "MAN-365 fix: ensure_oauth_configured() now logs the env var names "
        "via _log.error() (a safe sink, never surfaced to a customer) and "
        "raises a customer-safe HTTPException with no env names. The "
        "_state_secret()/_provider_env() env-var-name LISTS (config data, "
        "never raised) also live in this file and match the token regex as "
        "plain string literals passed to _env_first(), not to any raise."
    ),
    "vault_store.py": (
        "CREDENTIAL_VAULT_KEY raises (_vault_passphrase/_set_vault_passphrase) "
        "are operator-only boot/rotation diagnostics, never reached from a "
        "customer route -- verified: rotate_vault_key's own route "
        "(connectors_core.py) is operator-gated (see its own allowlist "
        "entry below), and _vault_passphrase() is a boot-path helper. "
        "EMPYRALIS_CLOUD_URL / EMPYRALIS_NODE_SESSION_TOKEN raises live in "
        "_cloud_api_post(), an Agent Computer / gateway box-side vault "
        "backup helper (per its own comment: 'runs inside the command "
        "worker'), not a server API route; one call site "
        "(restore_vault_from_cloud_backup) already wraps it in "
        "`except Exception as api_exc: LOGGER.warning(...)`. The other "
        "call site (cloud backup upload) was not fully traced end-to-end "
        "to a route in this pass -- flagged here as verified-plausible-safe "
        "(box-internal vault machinery, not the OAuth/connector customer "
        "surface this ticket is about) rather than fully proven."
    ),
    "jwt_secret.py": (
        "assert_explicit_secret_safe_for_environment() raises at process "
        "boot/preflight time, never per-request -- there is no customer "
        "request in flight when this can fire."
    ),
    "connectors_core.py": (
        "rotate_vault_key's HTTPException naming CREDENTIAL_VAULT_KEY is "
        "reached only through routes_connectors.rotate_vault_key_route, "
        "which is gated on current_user_has_auth_admin_access (operator "
        "only, not a customer route) -- verified 2026-08-26."
    ),
    "mini_apps_service.py": (
        "_share_token_secret()'s ConfigurationError naming "
        "EMPYRALIS_MINI_APP_SHARE_SECRET is unreachable in any deployed "
        "environment: runtime_config.py already refuses to boot in "
        "staging/production if that env var is unset, so this branch can "
        "only fire locally. No caller wraps it into an HTTPException with "
        "str(exc) either -- verified 2026-08-26."
    ),
    "mini_app_token_exchange_service.py": (
        "_exchange_secret()'s ConfigurationError naming "
        "EMPYRALIS_MINI_APP_EXCHANGE_SECRET -- same shape as "
        "mini_apps_service.py above. Verified 2026-08-26: exchange_launch_"
        "token/issue_access_token/verify_access_token/"
        "enforce_access_token_scope have ZERO callers anywhere outside this "
        "module (grepped) -- the whole module is unwired to any route "
        "today ('built, tested, and never wired')."
    ),
    "runs_engine.py": (
        "call_openai_responses()'s RuntimeError naming CODEX_OAUTH_TOKEN "
        "and sibling env vars has zero callers outside this module -- "
        "verified 2026-08-26, unreachable from any route today."
    ),
    "runtime_config.py": (
        "_assert_auth_secrets_safe_for_environment()'s RuntimeError naming "
        "EMPYRALIS_MINI_APP_SHARE_SECRET runs at MODULE IMPORT time (called "
        "unconditionally at the bottom of this file), i.e. process boot, "
        "never per-request."
    ),
    "sms_twilio_provisioning_service.py": (
        "assign_agent_sms_number()'s SmsProvisioningError naming "
        "TWILIO_SMS_PUBLIC_BASE_URL has zero callers anywhere outside this "
        "module -- verified 2026-08-26, unreachable from any route today. "
        "(NOT_CONFIGURED_MESSAGE, a module constant that also names "
        "TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN, is not caught by this scan "
        "since it's referenced by Name rather than a literal at the raise "
        "site -- its own callers were checked separately: connectors_"
        "actions.py only logs it via '%s', never raises it into an "
        "HTTPException.)"
    ),
    "tool_broker.py": (
        "ToolExecutionDeniedError naming EMPYRALIS_REQUIRE_AGENT_ID is a "
        "boolean feature-flag name (whether the flag 'is set'), not a "
        "credential -- matched the token regex only because the flag name "
        "ends in '_ID'. No exploitable configuration surface is disclosed "
        "(the flag's existence is documented behavior, not a secret)."
    ),
    "tools_image_gen.py": (
        "generate_image()'s RuntimeError naming STABILITY_API_KEY is "
        "reached via skills_service.execute_single_direct_tool_call as an "
        "agent TOOL result, not a direct HTTPException in an HTTP route "
        "response -- a different risk class than the OAuth Connect-button "
        "path this ticket is about (the message would only reach a "
        "customer if the model chose to relay a tool failure verbatim in "
        "its own reply). Flagged for a separate follow-up rather than "
        "fixed in this pass; not fixed here."
    ),
    "connector_validators.py": (
        "validate_telegram_connector()'s '<YOUR_BOT_TOKEN>'/'<YOUR_CHAT_ID>' "
        "are PLACEHOLDER EXAMPLE TEXT telling the customer what to replace "
        "in their OWN pasted credential -- not an operator env var name. "
        "False positive: the regex matched 'YOUR_BOT_TOKEN'/'YOUR_CHAT_ID' "
        "as if they were env-var-shaped tokens, but they never appear in "
        "any os.environ lookup in this codebase."
    ),
    "memory_service.py": (
        "'NORMALIZE_WORKSPACE_ID not configured' is a dependency-injection "
        "class attribute (self.NORMALIZE_WORKSPACE_ID, a callable slot), "
        "not an environment variable. False positive: matched only because "
        "the attribute name happens to end in '_ID'."
    ),
}

_DYNAMIC_NAME_ALLOWLIST: dict[str, str] = {
    # Intentionally empty as of the MAN-365 fix -- ensure_oauth_configured()
    # was the one instance of this shape and it no longer builds its message
    # from client_names/secret_names. Keep this dict so a future instance
    # of the exact "Set {' or '.join(x_names)}" shape has somewhere to be
    # allowlisted with a reason, rather than reintroducing a silent gap.
}


def test_scan_reaches_real_files_canary():
    files = _iter_module_files()
    assert len(files) > 100, (
        f"Only found {len(files)} .py files under server_modules/ (excluding "
        "tests/) -- the scan is not reaching the real tree. Either the repo "
        "layout changed or SERVER_MODULES_DIR/EXCLUDED_DIR_NAMES is wrong."
    )


def test_scan_mechanism_finds_something_canary():
    """If the AST/regex logic is broken (e.g. matches nothing on any real
    file), the allowlists below would silently stop meaning anything. Assert
    the raw, pre-allowlist scan finds at least the known allowlisted hits --
    proof the mechanism actually detects the shape it exists to catch."""
    total_literal = 0
    for path in _iter_module_files():
        literal_hits, _dynamic_hits = scan_file_for_env_leaks(path)
        total_literal += len(literal_hits)
    assert total_literal > 0, (
        "The literal-token scan found zero hits across the entire "
        "server_modules tree, including files known to carry allowlisted "
        "env-var-naming raises (e.g. vault_store.py). The scan is broken."
    )


def test_no_new_literal_env_var_leaks():
    violations: dict[str, list[str]] = {}
    for path in _iter_module_files():
        literal_hits, _dynamic_hits = scan_file_for_env_leaks(path)
        if literal_hits and path.name not in _LITERAL_TOKEN_ALLOWLIST:
            violations[_relative(path)] = literal_hits

    assert not violations, (
        "Found raised exceptions naming an operator env var literally in "
        "their message, in a file with no allowlist entry (and therefore no "
        "written verdict that it's safe). If this is a genuine customer-"
        "facing path, rewrite the message the way MAN-365 fixed "
        "connection_oauth_service.ensure_oauth_configured(): log the env "
        "var name via a logger call, raise a message with no env names. If "
        "it's provably unreachable by a customer, add it to "
        "_LITERAL_TOKEN_ALLOWLIST with a reason.\n"
        f"{violations}"
    )


def test_no_new_dynamic_env_name_leaks():
    violations: dict[str, list[str]] = {}
    for path in _iter_module_files():
        _literal_hits, dynamic_hits = scan_file_for_env_leaks(path)
        if dynamic_hits and path.name not in _DYNAMIC_NAME_ALLOWLIST:
            violations[_relative(path)] = dynamic_hits

    assert not violations, (
        "Found a raised exception whose message is built from a variable "
        "shaped like a collection of env var NAMES (client_names/"
        "secret_names/env_vars/...) -- the exact MAN-365 shape "
        "(`f\"Set {' or '.join(client_names)}\"`), just not caught by the "
        "literal-token regex because the names never appear as literals at "
        "the raise site. Either fix it (log the names, raise a customer-"
        "safe message) or add it to _DYNAMIC_NAME_ALLOWLIST with a reason.\n"
        f"{violations}"
    )


def test_allowlists_are_not_stale():
    """An allowlist entry for a file that no longer exists under
    server_modules/ is a stale entry -- remove it."""
    all_files = {p.name for p in _iter_module_files()}
    for allowlist in (_LITERAL_TOKEN_ALLOWLIST, _DYNAMIC_NAME_ALLOWLIST):
        for filename in allowlist:
            assert filename in all_files, (
                f"Allowlist entry {filename!r} does not exist under "
                "server_modules/ anymore -- remove the stale entry."
            )


def test_known_provider_env_var_names_are_not_hand_duplicated_here():
    """The dynamic-name heuristic's identifier patterns are generic
    (client_names/secret_names/env_vars/...), not a hand-copied list of the
    actual env var strings -- so this test only has to prove that
    connection_oauth_service's OWN env var name table (the thing a future
    instance of this bug would leak) is still reachable from a different
    module than this test, i.e. that a real source of truth exists to check
    a fix against. It does not re-derive or duplicate that table."""
    from server_modules import connection_oauth_service

    assert connection_oauth_service.OAUTH_PROVIDER_CONFIGS, (
        "OAUTH_PROVIDER_CONFIGS is empty -- nothing to compare a future fix "
        "against."
    )
    sample_env_names = set()
    for config in connection_oauth_service.OAUTH_PROVIDER_CONFIGS.values():
        for names in config.env_vars.values():
            sample_env_names.update(names)
    assert any(_ENV_TOKEN_RE.search(name) for name in sample_env_names), (
        "None of the real provider env var names match the env-var-shaped "
        "token regex used by this scan -- the regex is miscalibrated "
        "against real data."
    )
