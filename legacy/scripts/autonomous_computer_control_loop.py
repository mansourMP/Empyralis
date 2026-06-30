#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict, cast

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server_modules import supervisor_client  # noqa: E402
from server_modules.computer_action_safety import evaluate_dangerous_computer_action_policy  # noqa: E402

DEFAULT_MODEL = os.getenv("EMPYRALIS_AUTONOMOUS_CONTROL_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
MAX_STEPS = 20
MAX_HISTORY_ITEMS = 12
CLICK_CONFIRMATION_PADDING = 180
FOCUS_CONFIRMATION_PADDING = 240
TYPING_VERIFICATION_WAIT_SECONDS = 0.2
LOW_CONFIDENCE_GROUNDING_THRESHOLD = 0.58
LOW_CONFIDENCE_DANGEROUS_THRESHOLD = 0.72
MIN_STAGNANT_SCREENSHOTS = 3
VISUAL_STALL_STOP_SCREENSHOTS = 6
MAX_VISUAL_STALL_RECOVERY_ATTEMPTS = 1
MAX_LOADING_RECOVERY_ATTEMPTS = 2
MIN_REPEATED_ACTION_STREAK = 2
STOP_REPEATED_ACTION_STREAK = 4
MIN_TARGET_ACQUISITION_FAILURES = 2
STOP_TARGET_ACQUISITION_FAILURES = 4
LOADING_RECOVERY_WAIT_SECONDS = 1.5
VISUAL_RECOVERY_WAIT_SECONDS = 0.8
ADDRESS_BAR_KEYWORDS = (
    "address bar",
    "omnibox",
    "url bar",
    "location bar",
    "browser search",
    "search bar",
)
BROWSER_APP_NAMES = ("chrome", "google chrome", "safari", "arc", "firefox", "edge", "brave")
BROWSER_CONTEXT_KEYWORDS = (
    "browser",
    "google",
    "search",
    "website",
    "web page",
    "tab",
    "address bar",
    "url",
)
BROWSER_LOADING_MARKERS = (
    "loading",
    "please wait",
    "just a moment",
    "checking your browser",
    "one moment",
    "verifying",
)
BROWSER_INPUT_READY_MARKERS = (
    "search google or type a url",
    "type a url",
    "search or enter address",
    "address bar",
    "search",
    "google search",
)
BROWSER_READY_MARKERS = (
    "google",
    "gmail",
    "images",
    "maps",
    "news",
    "search results",
    "empyralis",
)


def _autonomous_policy_metadata() -> Dict[str, Any]:
    allowed_classes = [
        str(item or "").strip().lower()
        for item in str(os.getenv("EMPYRALIS_AUTONOMOUS_ALLOW_DANGEROUS_CLASSES") or "").split(",")
        if str(item or "").strip()
    ]
    metadata: Dict[str, Any] = {
        "owner_role": str(os.getenv("EMPYRALIS_AUTONOMOUS_ROLE") or "owner").strip().lower() or "owner",
        "owner_is_admin": str(os.getenv("EMPYRALIS_AUTONOMOUS_IS_ADMIN") or "1").strip().lower() not in {"0", "false", "no"},
        "auth_type": "api_key",
    }
    if allowed_classes:
        metadata["computer_action_policy"] = {"allow_dangerous_classes": allowed_classes}
    return metadata


class SupportAction(TypedDict, total=False):
    action: Literal["click", "key", "wait"]
    reason: str
    x: int
    y: int
    key: str
    seconds: float
    target_text: str
    target_description: str
    confirm_text: str


class PlannerAction(TypedDict, total=False):
    action: Literal["click", "type", "key", "launch_app", "wait", "done"]
    reason: str
    confidence: float
    certainty: Literal["low", "medium", "high"]
    x: int
    y: int
    text: str
    key: str
    target: str
    seconds: float
    target_text: str
    target_description: str
    confirm_text: str
    app_context: str
    readiness_hint: str
    focus_action: SupportAction
    fallback_action: SupportAction


class StuckSignal(TypedDict, total=False):
    signal: str
    count: int
    summary: str
    action_fingerprint: str


class StuckAnalysis(TypedDict, total=False):
    active: bool
    should_stop: bool
    status: str
    summary: str
    signals: List[StuckSignal]
    planner_guidance: str
    avoid_action_fingerprint: str
    recovery_action: SupportAction
    recovery_strategy: Dict[str, Any]


class ReadinessSnapshot(TypedDict, total=False):
    context: str
    state: str
    summary: str
    observed_text: str
    matched_markers: List[str]


def _find_supervisor_secret() -> str:
    existing = str(os.getenv("EMPYRALIS_SUPERVISOR_SECRET") or "").strip()
    if existing:
        return existing

    listener_result = subprocess.run(
        ["lsof", "-nP", "-iTCP:7788", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = [line.strip() for line in listener_result.stdout.splitlines() if line.strip()]
    if not pids:
        pid_result = subprocess.run(
            ["pgrep", "-f", "empyralis-supervisor"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = [line.strip() for line in pid_result.stdout.splitlines() if line.strip()]
    if not pids:
        raise RuntimeError("Could not find a running empyralis-supervisor process.")

    pid = pids[0]
    ps_result = subprocess.run(
        ["ps", "eww", "-p", pid],
        capture_output=True,
        text=True,
        check=False,
    )
    if ps_result.returncode != 0:
        raise RuntimeError(f"Failed to inspect supervisor environment for pid {pid}.")

    match = re.search(r"EMPYRALIS_SUPERVISOR_SECRET=([^ ]+)", ps_result.stdout)
    if not match:
        raise RuntimeError("EMPYRALIS_SUPERVISOR_SECRET not found in the active supervisor process.")
    secret = match.group(1).strip()
    if not secret:
        raise RuntimeError("EMPYRALIS_SUPERVISOR_SECRET was empty in the active supervisor process.")
    os.environ["EMPYRALIS_SUPERVISOR_SECRET"] = secret
    return secret


def _require_openai_api_key() -> None:
    if not str(os.getenv("OPENAI_API_KEY") or "").strip():
        raise RuntimeError("OPENAI_API_KEY is required for autonomous computer control.")


def _save_screenshot(payload: Dict[str, Any], destination: Path) -> Dict[str, Any]:
    images = payload.get("images")
    if not isinstance(images, list) or not images:
        raise RuntimeError("Supervisor screenshot returned no images.")
    image = images[0]
    if not isinstance(image, dict):
        raise RuntimeError("Supervisor screenshot returned invalid image metadata.")
    encoded = str(image.get("data_base64") or "").strip()
    if not encoded:
        raise RuntimeError("Supervisor screenshot returned empty image data.")
    destination.write_bytes(base64.b64decode(encoded))
    width = int(image.get("width") or 0)
    height = int(image.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("Supervisor screenshot did not report valid dimensions.")
    return {
        "path": str(destination),
        "width": width,
        "height": height,
        "data_base64": encoded,
        "signature": _screenshot_signature(encoded),
    }


def _history_text(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "No prior actions."
    lines: List[str] = []
    for item in history[-MAX_HISTORY_ITEMS:]:
        step = int(item.get("step") or 0)
        action = str(item.get("action") or "unknown")
        reason = str(item.get("reason") or "").strip()
        outcome = str(item.get("outcome") or "").strip()
        success = item.get("success")
        needs_replan = bool(item.get("needs_replan"))
        params = item.get("params")
        recovery_strategy = item.get("recovery_strategy")
        stuck_signals = item.get("stuck_signals")
        confidence = item.get("confidence")
        certainty = str(item.get("certainty") or "").strip()
        retry_count = item.get("retry_count")
        replanned = bool(item.get("replanned"))
        readiness = item.get("readiness")
        lines.append(
            f"- step {step}: {action}"
            + (f" params={json.dumps(params, ensure_ascii=True)}" if params else "")
            + (f" reason={reason}" if reason else "")
            + (f" success={success}" if success is not None else "")
            + (f" confidence={confidence:.2f}" if isinstance(confidence, (float, int)) else "")
            + (f" certainty={certainty}" if certainty else "")
            + (f" retry_count={retry_count}" if isinstance(retry_count, int) and retry_count > 1 else "")
            + (" needs_replan=true" if needs_replan else "")
            + (" replanned=true" if replanned else "")
            + (f" readiness={json.dumps(readiness, ensure_ascii=True)}" if isinstance(readiness, dict) and readiness else "")
            + (f" recovery={json.dumps(recovery_strategy, ensure_ascii=True)}" if recovery_strategy else "")
            + (f" stuck={json.dumps(stuck_signals, ensure_ascii=True)}" if stuck_signals else "")
            + (f" outcome={outcome}" if outcome else "")
        )
    return "\n".join(lines)


def _screenshot_signature(encoded: str) -> str:
    return hashlib.sha256(str(encoded or "").encode("utf-8")).hexdigest()[:16]


def _normalized_match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _normalized_confidence(value: Any) -> Optional[float]:
    try:
        confidence = float(value)
    except Exception:
        return None
    if not confidence == confidence:
        return None
    return max(0.0, min(confidence, 1.0))


def _normalized_certainty(value: Any, confidence: Optional[float]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    if confidence is None:
        return "medium"
    if confidence < LOW_CONFIDENCE_GROUNDING_THRESHOLD:
        return "low"
    if confidence >= 0.86:
        return "high"
    return "medium"


def _is_browser_like_text(value: str) -> bool:
    normalized = _normalized_match_text(value)
    if not normalized:
        return False
    if "http" in normalized or "www" in normalized:
        return True
    if any(keyword in normalized for keyword in BROWSER_CONTEXT_KEYWORDS):
        return True
    return any(app_name in normalized for app_name in BROWSER_APP_NAMES)


def _infer_app_context(goal: str, history: List[Dict[str, Any]], action: Optional[PlannerAction] = None) -> str:
    if action:
        for candidate in (
            action.get("app_context"),
            action.get("target_description"),
            action.get("target_text"),
            action.get("confirm_text"),
            action.get("target"),
        ):
            if _is_browser_like_text(str(candidate or "")):
                return "browser"
    if _is_browser_like_text(goal):
        return "browser"
    for item in reversed(history[-4:]):
        action_name = str(item.get("action") or "").strip().lower()
        if action_name in {"launch_app", "type", "key", "click"}:
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            text_candidates = [
                str(params.get("target") or ""),
                str(params.get("target_description") or ""),
                str(params.get("target_text") or ""),
                str(params.get("key") or ""),
                str(item.get("reason") or ""),
            ]
            if any(_is_browser_like_text(candidate) for candidate in text_candidates):
                return "browser"
    return "desktop"


def _browser_readiness_region(width: int, height: int) -> Dict[str, int]:
    return {
        "x": 0,
        "y": 0,
        "width": max(1, int(width)),
        "height": max(1, int(height * 0.34)),
    }


def _capture_readiness_snapshot(goal: str, history: List[Dict[str, Any]], screenshot: Dict[str, Any]) -> ReadinessSnapshot:
    context = _infer_app_context(goal, history)
    if context != "browser":
        return {
            "context": context,
            "state": "unknown",
            "summary": "No app-specific readiness probe was required.",
            "matched_markers": [],
        }
    region = _browser_readiness_region(int(screenshot["width"]), int(screenshot["height"]))
    ocr = _ocr_region_text(region)
    if not ocr.get("success"):
        return {
            "context": "browser",
            "state": "unknown",
            "summary": "Browser readiness probe was unavailable.",
            "observed_text": "",
            "matched_markers": [],
        }
    observed_text = str(ocr.get("text") or "").strip()
    normalized = _normalized_match_text(observed_text)
    matched_loading = [marker for marker in BROWSER_LOADING_MARKERS if _normalized_match_text(marker) in normalized]
    matched_input = [marker for marker in BROWSER_INPUT_READY_MARKERS if _normalized_match_text(marker) in normalized]
    matched_ready = [marker for marker in BROWSER_READY_MARKERS if _normalized_match_text(marker) in normalized]
    if matched_loading:
        return {
            "context": "browser",
            "state": "loading",
            "summary": f"Browser still looks busy: {matched_loading[0]}.",
            "observed_text": observed_text,
            "matched_markers": matched_loading,
        }
    if matched_input:
        return {
            "context": "browser",
            "state": "input_ready",
            "summary": f"Browser input looks ready: {matched_input[0]}.",
            "observed_text": observed_text,
            "matched_markers": matched_input,
        }
    if matched_ready:
        return {
            "context": "browser",
            "state": "ready",
            "summary": f"Browser content looks ready: {matched_ready[0]}.",
            "observed_text": observed_text,
            "matched_markers": matched_ready,
        }
    if len(normalized) < 8:
        return {
            "context": "browser",
            "state": "blank",
            "summary": "Browser screenshot contains very little visible text.",
            "observed_text": observed_text,
            "matched_markers": [],
        }
    return {
        "context": "browser",
        "state": "ready",
        "summary": "Browser screenshot contains visible page text.",
        "observed_text": observed_text,
        "matched_markers": [],
    }


def _browser_guidance_text(goal: str, history: List[Dict[str, Any]], readiness: Optional[ReadinessSnapshot]) -> str:
    context = _infer_app_context(goal, history)
    if context != "browser":
        return "None."
    lines = [
        "- This looks like a browser task.",
        "- Prefer: open page/app -> wait until browser is ready -> focus address/search bar intentionally -> type -> submit.",
        "- Use cmd+l for the address bar when appropriate instead of blind typing.",
    ]
    if readiness:
        lines.append(f"- Readiness: {str(readiness.get('state') or 'unknown')}.")
        summary = str(readiness.get("summary") or "").strip()
        if summary:
            lines.append(f"- Readiness summary: {summary}")
    return "\n".join(lines)


def _action_fingerprint(action: str, params: Any) -> str:
    normalized_action = str(action or "").strip().lower() or "unknown"
    normalized_params = params if isinstance(params, dict) else {}
    return f"{normalized_action}:{json.dumps(normalized_params, sort_keys=True, ensure_ascii=True)}"


def _history_action_fingerprint(item: Dict[str, Any]) -> str:
    return _action_fingerprint(str(item.get("action") or ""), item.get("params"))


def _is_loading_related_action(item: Dict[str, Any]) -> bool:
    strategy = item.get("recovery_strategy")
    if isinstance(strategy, dict) and strategy.get("signal") in {"loading_no_response", "visual_stall"}:
        return True
    return str(item.get("action") or "").strip().lower() in {"wait", "launch_app"}


def _extract_error_text(payload: Any) -> str:
    if isinstance(payload, dict):
        parts: List[str] = []
        for key, value in payload.items():
            if key == "error" and value:
                parts.append(str(value))
            else:
                nested = _extract_error_text(value)
                if nested:
                    parts.append(nested)
        return " ".join(part for part in parts if part).strip()
    if isinstance(payload, list):
        parts = [_extract_error_text(item) for item in payload]
        return " ".join(part for part in parts if part).strip()
    return ""


def _is_target_acquisition_failure(item: Dict[str, Any]) -> bool:
    if bool(item.get("success", True)):
        return False
    if str(item.get("action") or "").strip().lower() not in {"click", "type"}:
        return False
    error_text = _normalized_match_text(
        _extract_error_text(item.get("result_payload")) or str(item.get("outcome") or "")
    )
    if not error_text:
        return False
    markers = (
        "could not establish focus",
        "visible text near",
        "did not match",
        "could not find visible text",
        "target text",
        "focus",
    )
    return any(_normalized_match_text(marker) in error_text for marker in markers)


def _consecutive_identical_screenshot_count(history: List[Dict[str, Any]], current_signature: str) -> int:
    if not current_signature:
        return 0
    count = 1
    for item in reversed(history):
        if str(item.get("screenshot_signature") or "") != current_signature:
            break
        count += 1
    return count


def _consecutive_recovery_attempts(history: List[Dict[str, Any]], signal: str) -> int:
    count = 0
    for item in reversed(history):
        strategy = item.get("recovery_strategy")
        if not isinstance(strategy, dict):
            if count:
                break
            continue
        if str(strategy.get("signal") or "") != signal:
            break
        count += 1
    return count


def _consecutive_target_acquisition_failures(history: List[Dict[str, Any]]) -> tuple[int, str]:
    count = 0
    last_fingerprint = ""
    for item in reversed(history):
        if not _is_target_acquisition_failure(item):
            break
        count += 1
        if not last_fingerprint:
            last_fingerprint = _history_action_fingerprint(item)
    return count, last_fingerprint


def _repeated_action_no_progress_streak(history: List[Dict[str, Any]], current_signature: str) -> tuple[int, str]:
    if not history:
        return 0, ""
    streak = 0
    fingerprint = ""
    for item in reversed(history):
        if item.get("recovery_strategy"):
            if streak:
                break
            continue
        if str(item.get("screenshot_signature") or "") != current_signature:
            break
        current_fingerprint = _history_action_fingerprint(item)
        if not fingerprint:
            fingerprint = current_fingerprint
        if current_fingerprint != fingerprint:
            break
        streak += 1
    return streak, fingerprint


def _loading_no_progress_streak(history: List[Dict[str, Any]], current_signature: str) -> int:
    streak = 0
    for item in reversed(history):
        if str(item.get("screenshot_signature") or "") != current_signature:
            break
        if not _is_loading_related_action(item):
            break
        streak += 1
    return streak


def _analyze_stuck_state(
    history: List[Dict[str, Any]],
    screenshot: Dict[str, Any],
    readiness: Optional[ReadinessSnapshot] = None,
) -> StuckAnalysis:
    current_signature = str(screenshot.get("signature") or "")
    if not current_signature:
        return {"active": False, "signals": []}

    stagnant_count = _consecutive_identical_screenshot_count(history, current_signature)
    repeated_action_streak, repeated_fingerprint = _repeated_action_no_progress_streak(history, current_signature)
    target_failure_streak, target_failure_fingerprint = _consecutive_target_acquisition_failures(history)
    loading_streak = _loading_no_progress_streak(history, current_signature)

    if target_failure_streak >= MIN_TARGET_ACQUISITION_FAILURES:
        signal: StuckSignal = {
            "signal": "target_acquisition_failure",
            "count": target_failure_streak,
            "summary": f"Target acquisition has failed {target_failure_streak} times in a row.",
            "action_fingerprint": target_failure_fingerprint,
        }
        if target_failure_streak >= STOP_TARGET_ACQUISITION_FAILURES:
            return {
                "active": True,
                "should_stop": True,
                "status": "stuck_target_acquisition",
                "summary": "Stopped because target acquisition kept failing without progress.",
                "signals": [signal],
                "avoid_action_fingerprint": target_failure_fingerprint,
            }
        return {
            "active": True,
            "signals": [signal],
            "summary": signal["summary"],
            "planner_guidance": "Do not repeat the last failed target acquisition. Choose a different action or a safer focus path.",
            "avoid_action_fingerprint": target_failure_fingerprint,
        }

    readiness_state = str((readiness or {}).get("state") or "").strip().lower()

    if stagnant_count >= MIN_STAGNANT_SCREENSHOTS and loading_streak >= 2:
        signal = {
            "signal": "loading_no_response",
            "count": loading_streak,
            "summary": (
                f"The screen has not changed after {loading_streak} loading-related actions."
                + (f" Readiness probe: {readiness_state}." if readiness_state else "")
            ),
        }
        recovery_attempts = _consecutive_recovery_attempts(history, "loading_no_response")
        if readiness_state in {"ready", "input_ready"}:
            return {
                "active": True,
                "signals": [signal],
                "summary": signal["summary"],
                "planner_guidance": "The browser/app looks ready now. Stop waiting and choose the next visible action.",
            }
        if recovery_attempts < MAX_LOADING_RECOVERY_ATTEMPTS:
            return {
                "active": True,
                "signals": [signal],
                "summary": signal["summary"],
                "recovery_action": {
                    "action": "wait",
                    "seconds": LOADING_RECOVERY_WAIT_SECONDS,
                    "reason": "UI still appears to be loading. Wait and recheck before doing anything else.",
                },
                "recovery_strategy": {
                    "kind": "wait_and_recheck",
                    "signal": "loading_no_response",
                    "attempt": recovery_attempts + 1,
                },
            }
        return {
            "active": True,
            "should_stop": True,
            "status": "stuck_loading_timeout",
            "summary": "Stopped because the UI stayed in a loading/no-response state for too long.",
            "signals": [signal],
        }

    if stagnant_count >= MIN_STAGNANT_SCREENSHOTS and repeated_action_streak >= MIN_REPEATED_ACTION_STREAK:
        signal = {
            "signal": "repeated_action_no_progress",
            "count": repeated_action_streak,
            "summary": f"The same action repeated {repeated_action_streak} times without visible progress.",
            "action_fingerprint": repeated_fingerprint,
        }
        recovery_attempts = _consecutive_recovery_attempts(history, "repeated_action_no_progress")
        if recovery_attempts < 1:
            return {
                "active": True,
                "signals": [signal],
                "summary": signal["summary"],
                "recovery_action": {
                    "action": "wait",
                    "seconds": LOADING_RECOVERY_WAIT_SECONDS,
                    "reason": "The same action is repeating without progress. Wait once, then force a different plan.",
                },
                "recovery_strategy": {
                    "kind": "wait_and_recheck",
                    "signal": "repeated_action_no_progress",
                    "attempt": recovery_attempts + 1,
                },
                "avoid_action_fingerprint": repeated_fingerprint,
            }
        if repeated_action_streak >= STOP_REPEATED_ACTION_STREAK:
            return {
                "active": True,
                "should_stop": True,
                "status": "stuck_repeated_action",
                "summary": "Stopped because the loop kept choosing the same action with no visible progress.",
                "signals": [signal],
                "avoid_action_fingerprint": repeated_fingerprint,
            }
        return {
            "active": True,
            "signals": [signal],
            "summary": signal["summary"],
            "planner_guidance": "Choose a materially different action. Do not repeat the last action until the screen changes.",
            "avoid_action_fingerprint": repeated_fingerprint,
        }

    if stagnant_count >= MIN_STAGNANT_SCREENSHOTS:
        signal = {
            "signal": "visual_stall",
            "count": stagnant_count,
            "summary": f"The screenshot has been effectively unchanged for {stagnant_count} consecutive captures.",
        }
        recovery_attempts = _consecutive_recovery_attempts(history, "visual_stall")
        if recovery_attempts < MAX_VISUAL_STALL_RECOVERY_ATTEMPTS:
            return {
                "active": True,
                "signals": [signal],
                "summary": signal["summary"],
                "recovery_action": {
                    "action": "wait",
                    "seconds": VISUAL_RECOVERY_WAIT_SECONDS,
                    "reason": "The screen appears stalled. Pause briefly and take a fresh screenshot.",
                },
                "recovery_strategy": {
                    "kind": "wait_and_recheck",
                    "signal": "visual_stall",
                    "attempt": recovery_attempts + 1,
                },
            }
        if stagnant_count >= VISUAL_STALL_STOP_SCREENSHOTS:
            return {
                "active": True,
                "should_stop": True,
                "status": "stuck_visual_stall",
                "summary": "Stopped because the screen stayed unchanged and recovery attempts were exhausted.",
                "signals": [signal],
            }
        return {
            "active": True,
            "signals": [signal],
            "summary": signal["summary"],
            "planner_guidance": "The screen is unchanged. Pick a different action or declare done if the goal is already met.",
        }

    return {"active": False, "signals": []}


def _text_matches(observed: str, expected: str) -> bool:
    normalized_observed = _normalized_match_text(observed)
    normalized_expected = _normalized_match_text(expected)
    if not normalized_observed or not normalized_expected:
        return False
    if normalized_expected in normalized_observed:
        return True
    expected_tokens = [token for token in normalized_expected.split() if token]
    if not expected_tokens:
        return False
    long_tokens = [token for token in expected_tokens if len(token) >= 4]
    tokens_to_match = long_tokens or expected_tokens
    return all(token in normalized_observed for token in tokens_to_match)


def _region_around_point(x: int, y: int, width: int, height: int, padding: int) -> Dict[str, int]:
    left = max(0, int(x) - padding)
    top = max(0, int(y) - padding)
    right = min(width, int(x) + padding)
    bottom = min(height, int(y) + padding)
    return {
        "x": left,
        "y": top,
        "width": max(1, right - left),
        "height": max(1, bottom - top),
    }


def _ocr_region_text(region: Optional[Dict[str, int]]) -> Dict[str, Any]:
    try:
        result = supervisor_client.ocr(monitor="primary", region=region)
    except RuntimeError as exc:
        return {"success": False, "error": str(exc or "OCR failed.").strip(), "text": ""}
    text = str(result.get("text") or "").strip()
    return {"success": True, "text": text}


def _resolve_support_action(action: Any) -> Optional[SupportAction]:
    if not isinstance(action, dict):
        return None
    resolved: SupportAction = {}
    action_name = str(action.get("action") or "").strip().lower()
    if action_name not in {"click", "key", "wait"}:
        return None
    resolved["action"] = cast(Literal["click", "key", "wait"], action_name)
    for field in ("reason", "key", "target_text", "target_description", "confirm_text"):
        value = str(action.get(field) or "").strip()
        if value:
            resolved[field] = value  # type: ignore[typeddict-item]
    if "x" in action and action.get("x") is not None:
        resolved["x"] = int(action["x"])
    if "y" in action and action.get("y") is not None:
        resolved["y"] = int(action["y"])
    if "seconds" in action and action.get("seconds") is not None:
        resolved["seconds"] = float(action["seconds"])
    return resolved


def _default_focus_action(action: PlannerAction) -> Optional[SupportAction]:
    target_description = str(action.get("target_description") or "").strip().lower()
    if any(keyword in target_description for keyword in ADDRESS_BAR_KEYWORDS):
        return {
            "action": "key",
            "key": "cmd+l",
            "reason": "Focus the browser address/search bar before typing.",
            "target_description": str(action.get("target_description") or "").strip(),
        }
    target_text = str(action.get("target_text") or "").strip()
    if target_text:
        return {
            "action": "click",
            "target_text": target_text,
            "confirm_text": str(action.get("confirm_text") or target_text).strip(),
            "reason": "Focus the intended target before typing.",
            "target_description": str(action.get("target_description") or "").strip(),
        }
    return None


def _support_action_likely_establishes_focus(action: SupportAction, result: Dict[str, Any]) -> bool:
    if not result.get("success", False):
        return False
    action_name = str(action.get("action") or "").strip().lower()
    if action_name == "click":
        return True
    if action_name == "key":
        key_value = str(action.get("key") or "").strip().lower()
        return key_value in {"cmd+l", "ctrl+l", "tab", "shift+tab"}
    return False


def _execute_support_action(
    action: SupportAction,
    *,
    width: int,
    height: int,
    phase: str,
) -> Dict[str, Any]:
    action_name = str(action.get("action") or "").strip().lower()
    reason = str(action.get("reason") or "").strip()
    if action_name == "wait":
        seconds = float(action.get("seconds") or 1.0)
        bounded = max(0.2, min(seconds, 5.0))
        time.sleep(bounded)
        return {
            "success": True,
            "action": "wait",
            "phase": phase,
            "reason": reason,
            "params": {"seconds": bounded},
            "result": {"waited": bounded},
        }
    if action_name == "key":
        key = str(action.get("key") or "").strip()
        if not key:
            return {
                "success": False,
                "action": "key",
                "phase": phase,
                "reason": reason,
                "params": {},
                "result": {"error": "Focus key action was missing a key."},
            }
        result = supervisor_client.press_key(key)
        return {
            "success": True,
            "action": "key",
            "phase": phase,
            "reason": reason,
            "params": {"key": key},
            "result": result,
        }
    if action_name == "click":
        target_text = str(action.get("target_text") or "").strip()
        confirm_text = str(action.get("confirm_text") or "").strip()
        if target_text:
            result = supervisor_client.click(text=target_text)
            return {
                "success": True,
                "action": "click",
                "phase": phase,
                "reason": reason,
                "params": {"target_text": target_text},
                "result": result,
            }
        if action.get("x") is None or action.get("y") is None:
            return {
                "success": False,
                "action": "click",
                "phase": phase,
                "reason": reason,
                "params": {},
                "result": {"error": "Focus click action was missing x/y or target_text."},
            }
        x = int(action["x"])
        y = int(action["y"])
        if not (0 <= x < width and 0 <= y < height):
            return {
                "success": False,
                "action": "click",
                "phase": phase,
                "reason": reason,
                "params": {"x": x, "y": y},
                "result": {"error": f"Focus click coordinates were out of bounds: ({x}, {y})."},
            }
        confirmation: Dict[str, Any] = {"performed": False}
        if confirm_text:
            region = _region_around_point(x, y, width, height, CLICK_CONFIRMATION_PADDING)
            confirmation = _ocr_region_text(region)
            confirmation["performed"] = True
            confirmation["region"] = region
            confirmation["expected_text"] = confirm_text
            confirmation["matched"] = _text_matches(str(confirmation.get("text") or ""), confirm_text) if confirmation.get("success") else False
            if confirmation.get("success") and not confirmation.get("matched"):
                return {
                    "success": False,
                    "action": "click",
                    "phase": phase,
                    "reason": reason,
                    "params": {"x": x, "y": y},
                    "result": {"error": f"Visible text near ({x}, {y}) did not match '{confirm_text}'.", "confirmation": confirmation},
                }
        result = supervisor_client.click(x=x, y=y)
        return {
            "success": True,
            "action": "click",
            "phase": phase,
            "reason": reason,
            "params": {"x": x, "y": y},
            "result": {"click": result, "confirmation": confirmation},
        }
    return {
        "success": False,
        "action": action_name or "unknown",
        "phase": phase,
        "reason": reason,
        "params": {},
        "result": {"error": f"Unsupported support action: {action_name}"},
    }


def _establish_focus(action: PlannerAction, *, width: int, height: int) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    primary = _resolve_support_action(action.get("focus_action")) or _default_focus_action(action)
    fallback = _resolve_support_action(action.get("fallback_action"))
    focus_candidates = [candidate for candidate in (primary, fallback) if candidate]
    if not focus_candidates:
        return {"established": False, "attempts": attempts, "reason": "No focus action available."}

    for candidate in focus_candidates:
        support_result = _execute_support_action(candidate, width=width, height=height, phase="focus")
        attempts.append(support_result)
        if _support_action_likely_establishes_focus(candidate, support_result):
            return {
                "established": True,
                "attempts": attempts,
                "method": candidate.get("action"),
            }
    return {
        "established": False,
        "attempts": attempts,
        "reason": "Focus actions did not establish a reliable input target.",
    }


def _execute_click_fallback(action: PlannerAction, *, width: int, height: int) -> Optional[Dict[str, Any]]:
    fallback = _resolve_support_action(action.get("fallback_action"))
    if not fallback:
        return None
    return _execute_support_action(fallback, width=width, height=height, phase="fallback")


def _typing_verification_region(action: PlannerAction, *, width: int, height: int) -> Optional[Dict[str, int]]:
    focus_action = _resolve_support_action(action.get("focus_action")) or _default_focus_action(action)
    if focus_action and focus_action.get("action") == "click" and focus_action.get("x") is not None and focus_action.get("y") is not None:
        return _region_around_point(int(focus_action["x"]), int(focus_action["y"]), width, height, FOCUS_CONFIRMATION_PADDING)
    target_description = str(action.get("target_description") or "").strip().lower()
    if any(keyword in target_description for keyword in ADDRESS_BAR_KEYWORDS):
        return {"x": 0, "y": 0, "width": width, "height": max(1, int(height * 0.3))}
    return None


def _verify_typed_text(
    action: PlannerAction,
    *,
    typed_text: str,
    width: int,
    height: int,
) -> Dict[str, Any]:
    expected_text = str(action.get("confirm_text") or "").strip()
    if not expected_text:
        expected_text = typed_text[: min(32, len(typed_text))].strip()
    if not expected_text:
        return {"success": True, "checked": False, "reason": "No text available to verify."}

    time.sleep(TYPING_VERIFICATION_WAIT_SECONDS)
    region = _typing_verification_region(action, width=width, height=height)
    ocr_result = _ocr_region_text(region)
    verified = bool(ocr_result.get("success")) and _text_matches(str(ocr_result.get("text") or ""), expected_text)
    if verified:
        return {
            "success": True,
            "checked": True,
            "expected_text": expected_text,
            "region": region,
            "ocr_text": ocr_result.get("text"),
        }
    return {
        "success": False,
        "checked": bool(ocr_result.get("success")),
        "expected_text": expected_text,
        "region": region,
        "ocr_text": ocr_result.get("text"),
        "error": ocr_result.get("error") or f"Focused text did not visibly match '{expected_text}'.",
    }


def _stuck_context_text(stuck_context: Optional[StuckAnalysis]) -> str:
    if not stuck_context or not stuck_context.get("active"):
        return "None."
    lines = [
        f"- summary: {str(stuck_context.get('summary') or '').strip()}",
    ]
    for signal in stuck_context.get("signals") or []:
        lines.append(f"- signal: {json.dumps(signal, ensure_ascii=True)}")
    guidance = str(stuck_context.get("planner_guidance") or "").strip()
    if guidance:
        lines.append(f"- guidance: {guidance}")
    avoid = str(stuck_context.get("avoid_action_fingerprint") or "").strip()
    if avoid:
        lines.append(f"- avoid_action_fingerprint: {avoid}")
    strategy = stuck_context.get("recovery_strategy")
    if strategy:
        lines.append(f"- latest_recovery_strategy: {json.dumps(strategy, ensure_ascii=True)}")
    return "\n".join(lines)


def _readiness_context_text(readiness: Optional[ReadinessSnapshot]) -> str:
    if not readiness:
        return "None."
    lines = [
        f"- context: {str(readiness.get('context') or 'unknown')}",
        f"- state: {str(readiness.get('state') or 'unknown')}",
    ]
    summary = str(readiness.get("summary") or "").strip()
    if summary:
        lines.append(f"- summary: {summary}")
    observed_text = str(readiness.get("observed_text") or "").strip()
    compact_observed = observed_text[:240].strip()
    if compact_observed:
        lines.append(f"- observed_text: {observed_text}")
    markers = list(readiness.get("matched_markers") or [])
    if markers:
        lines.append(f"- matched_markers: {json.dumps(markers, ensure_ascii=True)}")
    return "\n".join(lines)


def _action_retry_count(history: List[Dict[str, Any]], action: str, params: Dict[str, Any]) -> int:
    fingerprint = _action_fingerprint(action, params)
    count = 1
    for item in reversed(history):
        if str(item.get("action_fingerprint") or "") != fingerprint:
            break
        count += 1
    return count


def _normalize_planner_action(goal: str, history: List[Dict[str, Any]], action: PlannerAction) -> PlannerAction:
    normalized: PlannerAction = dict(action)
    confidence = _normalized_confidence(action.get("confidence"))
    certainty = _normalized_certainty(action.get("certainty"), confidence)
    normalized["certainty"] = cast(Literal["low", "medium", "high"], certainty)
    if confidence is not None:
        normalized["confidence"] = confidence
    app_context = str(action.get("app_context") or "").strip().lower()
    if not app_context:
        app_context = _infer_app_context(goal, history, action)
    normalized["app_context"] = app_context
    readiness_hint = str(action.get("readiness_hint") or "").strip()
    if not readiness_hint and app_context == "browser":
        readiness_hint = "Use browser-aware focus and wait only while the page is visibly loading."
    if readiness_hint:
        normalized["readiness_hint"] = readiness_hint
    return normalized


def _planner_prompt(
    goal: str,
    width: int,
    height: int,
    history: List[Dict[str, Any]],
    step: int,
    stuck_context: Optional[StuckAnalysis] = None,
    readiness: Optional[ReadinessSnapshot] = None,
) -> str:
    return f"""You are an autonomous computer control planner for Empyralis.

Goal: {goal}
Current step: {step} of {MAX_STEPS}
Screen size: {width}x{height}

You must decide exactly one next action based on the current screenshot and action history.

Allowed actions:
- click: click absolute screen coordinates
- type: type text into a deliberately focused element
- key: press a key or key combo like cmd+l, enter, tab, esc
- launch_app: open an app or URL target through the local launcher
- wait: wait a short number of seconds for a page/app to load
- done: the goal is complete

Rules:
- Return JSON only.
- Prefer small, safe steps.
- Use click only when the screenshot clearly supports a coordinate choice or a visible target_text.
- For click, include confirm_text when visible text near the target can validate the click before it happens.
- For type, include target_description and a focus_action whenever focus might be ambiguous.
- If typing into a browser address/search bar, prefer focus_action={{"action":"key","key":"cmd+l"}}.
- Include confidence as a float from 0.0 to 1.0 and certainty as low|medium|high.
- Low-confidence clicks need stronger grounding. If you are not confident, prefer a visible target_text or confirm_text instead of a blind coordinate click.
- If the recent history shows focus verification failed, re-establish focus before typing again.
- Coordinates must be within the screen bounds.
- Use wait for loading transitions instead of repeating random actions.
- If stuck/recovery context is present, obey it. Do not repeat an avoided action fingerprint until the screen changes.
- Say done only when the goal is actually achieved.
- Do not invent capabilities outside the allowed actions.

JSON schema:
{{
  "action": "click|type|key|launch_app|wait|done",
  "reason": "short explanation",
  "confidence": 0.0,
  "certainty": "low|medium|high",
  "x": 0,
  "y": 0,
  "text": "text to type",
  "key": "cmd+l",
  "target": "Google Chrome|https://www.google.com",
  "seconds": 1.5,
  "target_text": "Search",
  "target_description": "browser address bar",
  "confirm_text": "Google",
  "app_context": "browser|desktop",
  "readiness_hint": "what you think the page/app is waiting for",
  "focus_action": {{
    "action": "key|click|wait",
    "key": "cmd+l",
    "x": 0,
    "y": 0,
    "target_text": "Search",
    "confirm_text": "Google"
  }},
  "fallback_action": {{
    "action": "key|click|wait",
    "key": "tab",
    "target_text": "Search"
  }}
}}

Recent history:
{_history_text(history)}

Stuck / recovery context:
{_stuck_context_text(stuck_context)}

App / page readiness:
{_readiness_context_text(readiness)}

App-aware guidance:
{_browser_guidance_text(goal, history, readiness)}
"""


def _extract_message_text(response: Any) -> str:
    message = response.choices[0].message
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()


def _plan_next_action(
    client: OpenAI,
    *,
    goal: str,
    screenshot: Dict[str, Any],
    history: List[Dict[str, Any]],
    step: int,
    model: str,
    stuck_context: Optional[StuckAnalysis] = None,
    readiness: Optional[ReadinessSnapshot] = None,
) -> PlannerAction:
    prompt = _planner_prompt(
        goal,
        screenshot["width"],
        screenshot["height"],
        history,
        step,
        stuck_context=stuck_context,
        readiness=readiness,
    )
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        max_completion_tokens=300,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "You analyze screenshots and choose the next UI action as strict JSON.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{screenshot['data_base64']}",
                        },
                    },
                ],
            },
        ],
    )
    raw = _extract_message_text(response)
    try:
        action = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Planner returned invalid JSON: {raw}") from exc
    if not isinstance(action, dict):
        raise RuntimeError(f"Planner returned non-object JSON: {raw}")
    return _normalize_planner_action(goal, history, cast(PlannerAction, action))


def _planned_action_policy_operation(action: PlannerAction) -> Dict[str, Any]:
    action_name = str(action.get("action") or "").strip().lower()
    payload: Dict[str, Any] = {
        "action": action_name,
        "reason": str(action.get("reason") or "").strip(),
        "target_text": str(action.get("target_text") or "").strip(),
        "target_description": str(action.get("target_description") or "").strip(),
        "confirm_text": str(action.get("confirm_text") or "").strip(),
    }
    if action_name == "click":
        if action.get("x") is not None:
            payload["x"] = int(action.get("x"))
        if action.get("y") is not None:
            payload["y"] = int(action.get("y"))
    elif action_name == "type":
        payload["text"] = str(action.get("text") or "")
    elif action_name == "launch_app":
        payload["target"] = str(action.get("target") or "").strip()
        payload["name_or_path"] = str(action.get("target") or "").strip()
    elif action_name == "key":
        payload["key"] = str(action.get("key") or "").strip()
    return payload


def _planned_action_capability_id(action: PlannerAction) -> str:
    action_name = str(action.get("action") or "").strip().lower()
    if action_name in {"click", "type"}:
        return f"computer_control.{action_name}"
    if action_name == "launch_app":
        return "computer_control.launch_app"
    if action_name == "key":
        return "computer_control.key"
    return ""


def _evaluate_planned_action_policy(action: PlannerAction) -> Dict[str, Any]:
    action_name = str(action.get("action") or "").strip().lower()
    if action_name in {"done", "wait"}:
        return {
            "decision": "allow",
            "execution_decision": "allow",
            "dangerous_action_classes": [],
            "dangerous_action_labels": [],
            "requires_approval": False,
            "reason": "non_dangerous_control_flow",
        }
    evaluation = evaluate_dangerous_computer_action_policy(
        _planned_action_policy_operation(action),
        metadata=_autonomous_policy_metadata(),
        capability_id=_planned_action_capability_id(action),
        policy_mode="local_default",
        target="local_companion",
    )
    confidence = _normalized_confidence(action.get("confidence"))
    dangerous_classes = list(evaluation.get("dangerous_action_classes") or [])
    if dangerous_classes and (confidence is None or confidence < LOW_CONFIDENCE_DANGEROUS_THRESHOLD):
        forced = dict(evaluation)
        forced["decision"] = "approval_required"
        forced["execution_decision"] = "require_confirmation"
        forced["requires_approval"] = True
        forced["reason"] = "low_confidence_dangerous_action"
        forced["planner_confidence"] = confidence
        forced["planner_certainty"] = _normalized_certainty(action.get("certainty"), confidence)
        return forced
    return evaluation


def _execute_action(action: PlannerAction, width: int, height: int) -> Dict[str, Any]:
    action_name = str(action.get("action") or "").strip().lower()
    reason = str(action.get("reason") or "").strip()
    confidence = _normalized_confidence(action.get("confidence"))
    certainty = _normalized_certainty(action.get("certainty"), confidence)

    if action_name == "click":
        target_text = str(action.get("target_text") or "").strip()
        confirm_text = str(action.get("confirm_text") or target_text).strip()
        if target_text and action.get("x") is None and action.get("y") is None:
            result = supervisor_client.click(text=target_text)
            return {
                "action": "click",
                "params": {"target_text": target_text},
                "result": {"click": result, "confirmation": {"performed": True, "matched_text": target_text}},
                "reason": reason,
                "success": True,
                "needs_replan": False,
            }
        if action.get("x") is None or action.get("y") is None:
            raise RuntimeError("Planner returned click action without x/y or target_text.")
        x = int(action.get("x"))
        y = int(action.get("y"))
        if not (0 <= x < width and 0 <= y < height):
            raise RuntimeError(f"Planner returned out-of-bounds click coordinates: ({x}, {y})")
        confirmation: Dict[str, Any] = {"performed": False}
        grounding_summary = "No extra visual grounding was required."
        if confidence is not None and confidence < LOW_CONFIDENCE_GROUNDING_THRESHOLD and not confirm_text and not target_text:
            return {
                "action": "click",
                "params": {"x": x, "y": y},
                "result": {
                    "error": "Low-confidence click did not include confirm_text or target_text for grounding.",
                    "grounding": {
                        "required": True,
                        "confidence": confidence,
                        "certainty": certainty,
                    },
                },
                "reason": reason,
                "success": False,
                "needs_replan": True,
            }
        if confirm_text:
            region = _region_around_point(x, y, width, height, CLICK_CONFIRMATION_PADDING)
            confirmation = _ocr_region_text(region)
            confirmation["performed"] = True
            confirmation["region"] = region
            confirmation["expected_text"] = confirm_text
            confirmation["matched"] = _text_matches(str(confirmation.get("text") or ""), confirm_text) if confirmation.get("success") else False
            grounding_summary = (
                f"Confirmed visible text near click target against '{confirm_text}'."
                if confirmation.get("matched")
                else f"Visual grounding near the click target did not match '{confirm_text}'."
            )
            if confirmation.get("success") and not confirmation.get("matched"):
                fallback_result = _execute_click_fallback(action, width=width, height=height)
                if fallback_result and fallback_result.get("success", False):
                    return {
                        "action": "click",
                        "params": {"x": x, "y": y, "confirm_text": confirm_text},
                        "result": {
                            "confirmation": confirmation,
                            "fallback": fallback_result,
                            "grounding": {
                                "summary": grounding_summary,
                                "confidence": confidence,
                                "certainty": certainty,
                            },
                        },
                        "reason": reason,
                        "success": True,
                        "needs_replan": False,
                    }
                return {
                    "action": "click",
                    "params": {"x": x, "y": y, "confirm_text": confirm_text},
                    "result": {
                        "error": f"Visible text near ({x}, {y}) did not match '{confirm_text}'.",
                        "confirmation": confirmation,
                        "fallback": fallback_result,
                        "grounding": {
                            "summary": grounding_summary,
                            "confidence": confidence,
                            "certainty": certainty,
                        },
                    },
                    "reason": reason,
                    "success": False,
                    "needs_replan": True,
                }
        elif confidence is not None and confidence < LOW_CONFIDENCE_GROUNDING_THRESHOLD:
            probe_region = _region_around_point(x, y, width, height, CLICK_CONFIRMATION_PADDING)
            probe = _ocr_region_text(probe_region)
            visible_text = str(probe.get("text") or "").strip()
            if not visible_text:
                return {
                    "action": "click",
                    "params": {"x": x, "y": y},
                    "result": {
                        "error": "Low-confidence click target had no visible OCR text for grounding.",
                        "grounding": {
                            "summary": "OCR grounding found no visible text near the proposed click target.",
                            "probe": probe,
                            "confidence": confidence,
                            "certainty": certainty,
                        },
                    },
                    "reason": reason,
                    "success": False,
                    "needs_replan": True,
                }
            grounding_summary = "Low-confidence click used OCR probe text to confirm that the region is visually populated."
        result = supervisor_client.click(x=x, y=y)
        return {
            "action": "click",
            "params": {"x": x, "y": y},
            "result": {
                "click": result,
                "confirmation": confirmation,
                "grounding": {
                    "summary": grounding_summary,
                    "confidence": confidence,
                    "certainty": certainty,
                },
            },
            "reason": reason,
            "success": True,
            "needs_replan": False,
        }

    if action_name == "type":
        text = str(action.get("text") or "")
        if not text:
            raise RuntimeError("Planner returned type action without text.")
        focus = _establish_focus(action, width=width, height=height)
        if not focus.get("established"):
            return {
                "action": "type",
                "params": {
                    "text": text,
                    "target_description": str(action.get("target_description") or "").strip() or None,
                    "target_text": str(action.get("target_text") or "").strip() or None,
                },
                "result": {
                    "error": "Could not establish focus before typing.",
                    "focus": focus,
                    "grounding": {
                        "summary": "Typing was blocked because focus could not be established.",
                        "confidence": confidence,
                        "certainty": certainty,
                    },
                },
                "reason": reason,
                "success": False,
                "needs_replan": True,
            }
        type_result = supervisor_client.type_text(text, delay_ms=20)
        verification = _verify_typed_text(action, typed_text=text, width=width, height=height)
        if not verification.get("success", False) and verification.get("checked", False):
            return {
                "action": "type",
                "params": {"text": text},
                "result": {
                    "type": type_result,
                    "focus": focus,
                    "verification": verification,
                    "grounding": {
                        "summary": "Post-type OCR verification failed.",
                        "confidence": confidence,
                        "certainty": certainty,
                    },
                },
                "reason": reason,
                "success": False,
                "needs_replan": True,
            }
        return {
            "action": "type",
            "params": {"text": text},
            "result": {
                "type": type_result,
                "focus": focus,
                "verification": verification,
                "grounding": {
                    "summary": "Typing was verified against visible text after focus was established.",
                    "confidence": confidence,
                    "certainty": certainty,
                },
            },
            "reason": reason,
            "success": True,
            "needs_replan": False,
        }

    if action_name == "key":
        key = str(action.get("key") or "").strip()
        if not key:
            raise RuntimeError("Planner returned key action without a key combo.")
        result = supervisor_client.press_key(key)
        return {"action": "key", "params": {"key": key}, "result": result, "reason": reason, "success": True, "needs_replan": False}

    if action_name == "launch_app":
        target = str(action.get("target") or "").strip()
        if not target:
            raise RuntimeError("Planner returned launch_app action without a target.")
        result = supervisor_client.launch_app(target)
        return {
            "action": "launch_app",
            "params": {"target": target},
            "result": {
                **result,
                "readiness": {
                    "hint": str(action.get("readiness_hint") or "").strip() or None,
                    "app_context": str(action.get("app_context") or "").strip() or None,
                },
            },
            "reason": reason,
            "success": True,
            "needs_replan": False,
        }

    if action_name == "wait":
        seconds = float(action.get("seconds") or 1.0)
        bounded = max(0.2, min(seconds, 5.0))
        time.sleep(bounded)
        return {"action": "wait", "params": {"seconds": bounded}, "result": {"waited": bounded}, "reason": reason, "success": True, "needs_replan": False}

    if action_name == "done":
        return {"action": "done", "params": {}, "result": {"done": True}, "reason": reason, "success": True, "needs_replan": False}

    raise RuntimeError(f"Planner returned unsupported action: {action_name}")


def _history_entry(
    *,
    history: List[Dict[str, Any]],
    step: int,
    screenshot: Dict[str, Any],
    screenshot_path: Path,
    action: str,
    params: Dict[str, Any],
    reason: str,
    success: bool,
    needs_replan: bool,
    result: Dict[str, Any],
    planned_action: Optional[Dict[str, Any]] = None,
    stuck_context: Optional[StuckAnalysis] = None,
    recovery_strategy: Optional[Dict[str, Any]] = None,
    readiness: Optional[ReadinessSnapshot] = None,
) -> Dict[str, Any]:
    retry_count = _action_retry_count(history, action, params)
    planner_confidence = _normalized_confidence((planned_action or {}).get("confidence"))
    planner_certainty = _normalized_certainty((planned_action or {}).get("certainty"), planner_confidence)
    grounding = result.get("grounding") if isinstance(result.get("grounding"), dict) else None
    return {
        "step": step,
        "action": action,
        "params": params,
        "reason": reason,
        "success": success,
        "needs_replan": needs_replan,
        "outcome": json.dumps(result, ensure_ascii=True),
        "result_payload": result,
        "screenshot": str(screenshot_path),
        "screenshot_signature": str(screenshot.get("signature") or ""),
        "action_fingerprint": _action_fingerprint(action, params),
        "stuck_signals": list(stuck_context.get("signals") or []) if stuck_context else [],
        "recovery_strategy": dict(recovery_strategy or {}),
        "planned_action": planned_action,
        "retry_count": retry_count,
        "replanned": bool(needs_replan or stuck_context and stuck_context.get("active")),
        "confidence": planner_confidence,
        "certainty": planner_certainty,
        "readiness": dict(readiness or {}),
        "grounding": dict(grounding or {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous screenshot -> vision -> supervisor action loop.")
    parser.add_argument("goal", help="Goal for the autonomous computer control loop.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Vision model to use. Default: {DEFAULT_MODEL}")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS, help=f"Maximum steps. Default: {MAX_STEPS}")
    args = parser.parse_args()

    _find_supervisor_secret()
    _require_openai_api_key()
    client = OpenAI()

    output_dir = ROOT / "tmp" / "autonomous-computer-control"
    output_dir.mkdir(parents=True, exist_ok=True)

    history: List[Dict[str, Any]] = []
    final_status = "max_steps_reached"
    final_stuck_report: Optional[StuckAnalysis] = None

    for step in range(1, max(1, min(args.max_steps, MAX_STEPS)) + 1):
        screenshot_payload = supervisor_client.capture_screenshot()
        screenshot_path = output_dir / f"step-{step:02d}.png"
        screenshot = _save_screenshot(screenshot_payload, screenshot_path)
        readiness = _capture_readiness_snapshot(args.goal, history, screenshot)

        stuck_context = _analyze_stuck_state(history, screenshot, readiness=readiness)

        if stuck_context.get("should_stop"):
            final_status = str(stuck_context.get("status") or "stuck")
            final_stuck_report = stuck_context
            stop_result = {
                "error": str(stuck_context.get("summary") or "Autonomous loop stopped because it was stuck."),
                "stuck": stuck_context,
            }
            history.append(
                _history_entry(
                    history=history,
                    step=step,
                    screenshot=screenshot,
                    screenshot_path=screenshot_path,
                    action="stuck_stop",
                    params={},
                    reason=str(stuck_context.get("summary") or "").strip(),
                    success=False,
                    needs_replan=False,
                    result=stop_result,
                    stuck_context=stuck_context,
                    readiness=readiness,
                )
            )
            print(
                json.dumps(
                    {
                        "step": step,
                        "screenshot": str(screenshot_path),
                        "executed_action": "stuck_stop",
                        "success": False,
                        "needs_replan": False,
                        "readiness": readiness,
                        "stuck": stuck_context,
                    },
                    indent=2,
                )
            )
            break

        if stuck_context.get("recovery_action"):
            recovery_action = cast(SupportAction, stuck_context["recovery_action"])
            recovery_strategy = cast(Dict[str, Any], stuck_context.get("recovery_strategy") or {})
            recovery = _execute_support_action(
                recovery_action,
                width=screenshot["width"],
                height=screenshot["height"],
                phase="recovery",
            )
            history.append(
                _history_entry(
                    history=history,
                    step=step,
                    screenshot=screenshot,
                    screenshot_path=screenshot_path,
                    action=str(recovery.get("action") or "wait"),
                    params=cast(Dict[str, Any], recovery.get("params") or {}),
                    reason=str(recovery.get("reason") or "").strip(),
                    success=bool(recovery.get("success", False)),
                    needs_replan=not bool(recovery.get("success", False)),
                    result=cast(Dict[str, Any], recovery.get("result") or {}),
                    planned_action={"action": "recovery"},
                    stuck_context=stuck_context,
                    recovery_strategy=recovery_strategy,
                    readiness=readiness,
                )
            )
            print(
                json.dumps(
                    {
                        "step": step,
                        "screenshot": str(screenshot_path),
                        "planned_action": {"action": "recovery"},
                        "executed_action": recovery.get("action"),
                        "success": recovery.get("success", False),
                        "needs_replan": not bool(recovery.get("success", False)),
                        "readiness": readiness,
                        "recovery_strategy": recovery_strategy,
                        "stuck": stuck_context,
                        "result": recovery.get("result") or {},
                    },
                    indent=2,
                )
            )
            continue

        planned = _plan_next_action(
            client,
            goal=args.goal,
            screenshot=screenshot,
            history=history,
            step=step,
            model=args.model,
            stuck_context=stuck_context,
            readiness=readiness,
        )
        policy_gate = _evaluate_planned_action_policy(planned)
        policy_decision = str(policy_gate.get("execution_decision") or policy_gate.get("decision") or "allow").strip().lower()
        if policy_decision in {"deny", "require_confirmation"}:
            final_status = "policy_blocked" if policy_decision == "deny" else "approval_required"
            policy_result = {
                "error": (
                    "Dangerous computer action blocked by policy."
                    if policy_decision == "deny"
                    else "Dangerous computer action requires approval before execution."
                ),
                "policy": policy_gate,
            }
            history.append(
                _history_entry(
                    history=history,
                    step=step,
                    screenshot=screenshot,
                    screenshot_path=screenshot_path,
                    action="policy_gate",
                    params=_planned_action_policy_operation(planned),
                    reason=str(planned.get("reason") or "").strip(),
                    success=False,
                    needs_replan=False,
                    result=policy_result,
                    planned_action=cast(Dict[str, Any], planned),
                    stuck_context=stuck_context,
                    readiness=readiness,
                )
            )
            print(
                json.dumps(
                    {
                        "step": step,
                        "screenshot": str(screenshot_path),
                        "planned_action": planned,
                        "executed_action": "policy_gate",
                        "success": False,
                        "needs_replan": False,
                        "readiness": readiness,
                        "stuck": stuck_context,
                        "result": policy_result,
                    },
                    indent=2,
                )
            )
            break
        executed = _execute_action(planned, screenshot["width"], screenshot["height"])
        history.append(
            _history_entry(
                history=history,
                step=step,
                screenshot=screenshot,
                screenshot_path=screenshot_path,
                action=cast(str, executed["action"]),
                params=cast(Dict[str, Any], executed["params"]),
                reason=cast(str, executed["reason"]),
                success=bool(executed.get("success", False)),
                needs_replan=bool(executed.get("needs_replan", False)),
                result=cast(Dict[str, Any], executed["result"]),
                planned_action=cast(Dict[str, Any], planned),
                stuck_context=stuck_context,
                readiness=readiness,
            )
        )

        print(
            json.dumps(
                {
                    "step": step,
                    "screenshot": str(screenshot_path),
                    "planned_action": planned,
                    "executed_action": executed["action"],
                    "success": executed.get("success"),
                    "needs_replan": executed.get("needs_replan", False),
                    "readiness": readiness,
                    "stuck": stuck_context,
                    "result": executed["result"],
                },
                indent=2,
            )
        )

        if executed["action"] == "done":
            final_status = "done"
            break

    print(
        json.dumps(
            {
                "ok": final_status == "done",
                "status": final_status,
                "goal": args.goal,
                "steps_executed": len(history),
                "artifacts_dir": str(output_dir),
                "stuck_report": final_stuck_report,
                "history": history,
            },
            indent=2,
        )
    )
    return 0 if final_status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
