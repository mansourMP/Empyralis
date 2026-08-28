"""Classify a provider HTTP response into ONE named failure, or nothing.

WHY THIS EXISTS
---------------
``OpenAICompatibleAdapter.generate`` used to read exactly one thing out of a
provider response — ``body["choices"]`` — and raise
``"<Provider> returned no choices."`` whenever it was missing or empty. It
never looked at ``res["status"]`` or ``body["error"]``, which
``runtime_common.http_json_request`` deliberately preserves (its own comment
says so, so that bad credentials still surface the provider's status code).

So SEVEN different facts shared one sentence. Measured against a stub
replying with the byte-exact bodies real providers send:

    HTTP 401 bad key            ─┐
    HTTP 402 empty balance       │
    HTTP 429 rate limited        ├─▶  "DeepSeek returned no choices."
    HTTP 404 no such model       │
    HTTP 500 provider down       │
    HTTP 400 malformed request   │
    HTTP 200 genuinely empty    ─┘    ← the ONLY one that sentence is true of

Nine providers share that adapter (groq, openrouter, xai, azure_openai,
qwen, deepseek, mistral, custom_openai_compatible, ollama) and DeepSeek is
the platform-credits provider — so this was the message every credits
customer got when the platform key died or its balance emptied. Preflight
already names that exact scenario in plain language at boot
(``_check_platform_credit_keys``, "PLATFORM-CREDIT KEY DEAD"); the runtime
threw the same information away one layer down.

That is the outcome-honesty law (CLAUDE.md): two different facts may never
share one signal. "Your key is dead", "your balance is empty" and "the
model answered with nothing" send a person to three different places.

THE VOCABULARY IS NOT NEW — THAT IS THE POINT
---------------------------------------------
Every code below is already a keyword in ``agent_command_dispatcher.
classify_error``, the single source of truth for customer-facing error
replies, and is already produced by ``direct_chat_generation_service.
_public_generation_error_code`` on the chat path. Nothing here invents a
second name for a failure that already has one — two engines with two
different names for the same failure is the bug this fixes, not a shape to
copy.

``test_provider_failure_classification.py`` proves that by driving the REAL
``classify_error`` with each code and asserting the reply it selects. The
expected set and the actual set therefore come from different modules; a
code that stops routing correctly fails loudly instead of quietly
degrading to "Something went wrong."

THE CODE LEADS THE MESSAGE, DELIBERATELY
----------------------------------------
``str(exc)`` is what survives every boundary this error crosses — a trace
event payload, a run's ``error_text``, a channel reply. A structured
attribute does not. So the stable code is the first token of the message,
matching the convention ``_public_generation_error_code`` already relies on
(``detail.startswith("provider_")`` → the detail IS the code). ``.code`` is
also exposed on the exception for callers holding the object itself.

PROVIDER PROSE NEVER REACHES THE MESSAGE
----------------------------------------
DeepSeek's own 401 body is ``"Your api key: ****cked is invalid"`` — a
partial key, echoed back. Anthropic's SDK path relays that prose into the
trace verbatim (redacted and truncated, but still relayed). Here the
provider's text goes on ``.provider_detail`` for logs and operators only,
redacted through ``secret_redaction_service`` first; the customer-facing
sentence is ours, in the product's voice. A stable code plus our own
sentence cannot leak a provider's response, however that response is
worded next month.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from server_modules import secret_redaction_service

# ── The codes. Each one is an existing classify_error keyword. ──────────────
#
# Ordering note that is load-bearing: classify_error tests its buckets in
# order and the FIRST match wins. PAYMENT_REQUIRED is checked before the
# auth bucket there, deliberately, because "provider_generation_failed" is
# itself an auth-bucket keyword and an empty balance must never be reported
# as an authentication problem. Codes here are chosen so each one matches
# its OWN bucket and no earlier one.

AUTH_FAILED = "provider_auth_failed"                 # → auth bucket (contains "auth")
PAYMENT_REQUIRED = "provider_payment_required"       # → payment bucket (exact keyword)
RATE_LIMITED = "provider_rate_limited"               # → rate-limit bucket (exact keyword)
MODEL_NOT_FOUND = "provider_model_not_found"         # → model bucket (exact keyword)
UNREACHABLE = "provider_transport_unavailable"       # → unreachable bucket (exact keyword)
REQUEST_REJECTED = "provider_request_rejected"       # → catch-all, correctly: our bug, not theirs
EMPTY_RESPONSE = "provider_empty_response"           # → catch-all, correctly: nothing is broken

# Every code this module can produce. Used by the drift test to prove each
# one routes somewhere deliberate rather than by accident.
ALL_CODES: Tuple[str, ...] = (
    AUTH_FAILED,
    PAYMENT_REQUIRED,
    RATE_LIMITED,
    MODEL_NOT_FOUND,
    UNREACHABLE,
    REQUEST_REJECTED,
    EMPTY_RESPONSE,
)


class ProviderCallError(RuntimeError):
    """A named provider failure.

    ``str(exc)`` is ``"<code>: <our sentence>"`` — the code first, because
    that string is all that survives being written to a trace payload or a
    run's error_text.

    ``provider_detail`` is the provider's own words, redacted. It is for
    logs and operators; it is never part of the message.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: Optional[int] = None,
        provider_detail: str = "",
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.sentence = message
        self.status = status
        self.provider_detail = provider_detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ProviderCallError(code={self.code!r}, status={self.status!r})"


def _provider_error_text(body: Any) -> str:
    """Pull the provider's own error prose out of an OpenAI-shaped body.

    Every provider behind OpenAICompatibleAdapter nests it under "error",
    but they disagree on whether that is an object or a bare string, and a
    few put a top-level "message" instead. Returns "" when there is nothing
    quotable rather than inventing a description.
    """
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, str):
        return error.strip()
    if isinstance(error, dict):
        for key in ("message", "detail", "description"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("message", "detail", "error_message"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _provider_error_code(body: Any) -> str:
    """The provider's OWN machine-readable code, when it sends one.

    This is what separates two failures that share an HTTP status: OpenAI
    answers 429 for BOTH ordinary rate limiting and a hard "you are out of
    credit" (``insufficient_quota``). Those need opposite advice — "try
    again in a moment" versus "top the account up" — so the status alone
    cannot decide, and guessing from prose is the stale-string-matching
    trap this codebase already documents. Read their field.
    """
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        for key in ("code", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    value = body.get("code")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return ""


# Provider codes that mean "the account behind this key has no money left",
# whatever HTTP status they arrive under. Matched as whole tokens against
# the provider's OWN code field, never against free prose.
_QUOTA_CODES = frozenset(
    {
        "insufficient_quota",
        "insufficient_balance",
        "quota_exceeded",
        "billing_hard_limit_reached",
        "credit_limit_exceeded",
        "payment_required",
    }
)

_MODEL_CODES = frozenset(
    {
        "model_not_found",
        "model_not_available",
        "unknown_model",
        "invalid_model",
    }
)


def classify_provider_http_failure(
    status: Any,
    body: Any,
    *,
    provider_label: str,
    model: str = "",
) -> Optional[ProviderCallError]:
    """Return the named failure this response represents, or None if it is fine.

    None means "this is a 2xx that may well carry a real answer" — the
    caller still has to check whether it actually did. Every other return
    is a failure that already knows its own name.
    """
    try:
        code_status = int(status)
    except (TypeError, ValueError):
        code_status = 0

    detail_raw = _provider_error_text(body)
    detail = secret_redaction_service.redact_text(detail_raw)[:400] if detail_raw else ""
    provider_code = _provider_error_code(body)
    label = str(provider_label or "The model provider").strip() or "The model provider"
    model_name = str(model or "").strip()

    def fail(code: str, sentence: str) -> ProviderCallError:
        return ProviderCallError(code, sentence, status=code_status, provider_detail=detail)

    # A provider that says "out of quota" is out of quota whatever status it
    # chose to say it under. Checked FIRST for the same reason classify_error
    # checks its own payment bucket before its auth bucket.
    if provider_code in _QUOTA_CODES:
        return fail(
            PAYMENT_REQUIRED,
            f"The {label} account behind this key is out of balance, so the request was refused.",
        )

    if code_status in (401, 403):
        return fail(
            AUTH_FAILED,
            f"{label} rejected the API key for this workspace.",
        )

    if code_status == 402:
        return fail(
            PAYMENT_REQUIRED,
            f"The {label} account behind this key is out of balance, so the request was refused.",
        )

    if code_status == 429:
        return fail(
            RATE_LIMITED,
            f"{label} is rate limiting this workspace right now.",
        )

    if code_status == 404 or provider_code in _MODEL_CODES:
        named = f" named {model_name!r}" if model_name else ""
        return fail(
            MODEL_NOT_FOUND,
            f"{label} has no model{named} available on this key.",
        )

    if code_status == 408 or code_status >= 500:
        return fail(
            UNREACHABLE,
            f"{label} could not complete the request (its own service returned an error).",
        )

    if code_status >= 400:
        return fail(
            REQUEST_REJECTED,
            f"{label} refused the request as malformed.",
        )

    return None


def empty_response_error(provider_label: str, *, status: Any = 200) -> ProviderCallError:
    """The ONE case "returned no choices" was ever honest about.

    A 2xx carrying no completion. Nothing is misconfigured and nothing is
    down — the provider answered, and the answer was empty. Kept as its own
    named outcome so it can never again be the sentence six real failures
    hide behind.
    """
    label = str(provider_label or "The model provider").strip() or "The model provider"
    try:
        code_status = int(status)
    except (TypeError, ValueError):
        code_status = 200
    return ProviderCallError(
        EMPTY_RESPONSE,
        f"{label} accepted the request and returned no completion.",
        status=code_status,
    )
