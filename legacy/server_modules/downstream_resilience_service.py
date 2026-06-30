from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, TypeVar


T = TypeVar("T")


class DownstreamResilienceError(RuntimeError):
    pass


class DownstreamCircuitOpenError(DownstreamResilienceError):
    pass


class RetryableDownstreamResponse(DownstreamResilienceError):
    def __init__(self, *, name: str, response: Mapping[str, Any]):
        self.name = str(name or "downstream").strip() or "downstream"
        self.response = dict(response)
        status = int(self.response.get("status") or 0)
        super().__init__(f"Retryable downstream response for {self.name}: HTTP {status}")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    initial_delay_seconds: float = 0.05
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 2.0


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    reset_after_seconds: float = 30.0


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: Optional[float] = None


_CIRCUITS: dict[str, _CircuitState] = {}
_RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def reset_circuits_for_tests() -> None:
    _CIRCUITS.clear()


def _circuit_state(name: str) -> _CircuitState:
    return _CIRCUITS.setdefault(str(name or "downstream").strip() or "downstream", _CircuitState())


def call_with_retries(
    *,
    name: str,
    operation: Callable[[], T],
    retry_policy: RetryPolicy = RetryPolicy(),
    circuit_policy: CircuitBreakerPolicy = CircuitBreakerPolicy(),
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> T:
    circuit_name = str(name or "downstream").strip() or "downstream"
    state = _circuit_state(circuit_name)
    current_time = now()
    if state.opened_at is not None:
        if current_time - state.opened_at < circuit_policy.reset_after_seconds:
            raise DownstreamCircuitOpenError(f"Downstream circuit is open for {circuit_name}.")
        state.failures = 0
        state.opened_at = None

    attempts = max(int(retry_policy.attempts or 1), 1)
    delay = max(float(retry_policy.initial_delay_seconds or 0), 0)
    last_error: BaseException | None = None
    for index in range(attempts):
        try:
            result = operation()
            state.failures = 0
            state.opened_at = None
            return result
        except BaseException as exc:
            last_error = exc
            if index < attempts - 1 and delay > 0:
                sleep(min(delay, max(float(retry_policy.max_delay_seconds or delay), 0)))
                delay *= max(float(retry_policy.backoff_multiplier or 1), 1)

    state.failures += 1
    if state.failures >= max(int(circuit_policy.failure_threshold or 1), 1):
        state.opened_at = now()
    if last_error is not None:
        raise last_error
    raise DownstreamResilienceError(f"Downstream operation failed for {circuit_name}.")


def is_retryable_http_status(status: Any) -> bool:
    try:
        normalized = int(status)
    except Exception:
        return False
    return normalized in _RETRYABLE_HTTP_STATUSES


def retry_after_seconds_from_headers(headers: Any) -> Optional[int]:
    if not isinstance(headers, Mapping):
        return None
    value = None
    for key, candidate in headers.items():
        if str(key or "").strip().lower() == "retry-after":
            value = candidate
            break
    try:
        seconds = int(float(str(value or "").strip()))
    except Exception:
        return None
    return max(seconds, 0)


def call_http_json_with_retries(
    *,
    name: str,
    operation: Callable[[], Mapping[str, Any]],
    retry_policy: RetryPolicy = RetryPolicy(attempts=2, initial_delay_seconds=0.15, max_delay_seconds=1.0),
    circuit_policy: CircuitBreakerPolicy = CircuitBreakerPolicy(failure_threshold=3, reset_after_seconds=30.0),
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    last_retryable_response: dict[str, Any] | None = None

    def _operation() -> dict[str, Any]:
        nonlocal last_retryable_response
        response = dict(operation())
        if is_retryable_http_status(response.get("status")):
            last_retryable_response = response
            retry_after = retry_after_seconds_from_headers(response.get("headers"))
            if retry_after is not None and retry_after > 0:
                sleep(min(float(retry_after), retry_policy.max_delay_seconds))
            raise RetryableDownstreamResponse(name=name, response=response)
        return response

    try:
        return call_with_retries(
            name=name,
            operation=_operation,
            retry_policy=retry_policy,
            circuit_policy=circuit_policy,
            sleep=sleep,
            now=now,
        )
    except RetryableDownstreamResponse as exc:
        return dict(last_retryable_response or exc.response)
