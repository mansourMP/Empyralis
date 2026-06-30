from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "autonomous_computer_control_loop.py"
SPEC = importlib.util.spec_from_file_location("autonomous_computer_control_loop_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
autonomous_loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(autonomous_loop)


def test_default_focus_action_uses_cmd_l_for_browser_address_bar():
    action = {
        "action": "type",
        "text": "Empyralis AI",
        "target_description": "browser address bar",
    }

    resolved = autonomous_loop._default_focus_action(action)

    assert resolved is not None
    assert resolved["action"] == "key"
    assert resolved["key"] == "cmd+l"


def test_click_confirmation_failure_uses_fallback_target_text():
    action = {
        "action": "click",
        "x": 640,
        "y": 320,
        "confirm_text": "Search",
        "fallback_action": {
            "action": "click",
            "target_text": "Search",
            "reason": "Fallback to OCR-targeted click.",
        },
    }

    with patch.object(autonomous_loop.supervisor_client, "ocr", return_value={"text": "I am feeling lucky"}), patch.object(
        autonomous_loop.supervisor_client,
        "click",
        return_value={"clicked": True, "matched_text": "Search"},
    ) as click_mock:
        result = autonomous_loop._execute_action(action, width=1440, height=900)

    assert result["success"] is True
    assert result["needs_replan"] is False
    assert result["result"]["fallback"]["action"] == "click"
    click_mock.assert_called_once_with(text="Search")


def test_type_action_infers_focus_and_verifies_visible_text():
    action = {
        "action": "type",
        "text": "Empyralis AI",
        "target_description": "browser address bar",
        "confirm_text": "Empyralis AI",
    }

    with patch.object(autonomous_loop.supervisor_client, "press_key", return_value={"pressed": True}) as key_mock, patch.object(
        autonomous_loop.supervisor_client,
        "type_text",
        return_value={"typed": True, "length": 13},
    ) as type_mock, patch.object(
        autonomous_loop.supervisor_client,
        "ocr",
        return_value={"text": "Empyralis AI - Google Search"},
    ):
        result = autonomous_loop._execute_action(action, width=1512, height=982)

    assert result["success"] is True
    assert result["needs_replan"] is False
    assert result["result"]["focus"]["established"] is True
    assert result["result"]["verification"]["success"] is True
    key_mock.assert_called_once_with("cmd+l")
    type_mock.assert_called_once_with("Empyralis AI", delay_ms=20)


def test_type_action_requests_replan_when_focus_verification_fails():
    action = {
        "action": "type",
        "text": "Empyralis AI",
        "focus_action": {
            "action": "key",
            "key": "cmd+l",
        },
        "confirm_text": "Empyralis AI",
    }

    with patch.object(autonomous_loop.supervisor_client, "press_key", return_value={"pressed": True}), patch.object(
        autonomous_loop.supervisor_client,
        "type_text",
        return_value={"typed": True, "length": 13},
    ), patch.object(
        autonomous_loop.supervisor_client,
        "ocr",
        return_value={"text": "Bookmarks Favorites Reading List"},
    ):
        result = autonomous_loop._execute_action(action, width=1512, height=982)

    assert result["success"] is False
    assert result["needs_replan"] is True
    assert result["result"]["verification"]["success"] is False


def test_analyze_stuck_state_requests_wait_for_loading_no_response():
    history = [
        {
            "action": "launch_app",
            "params": {"target": "https://google.com"},
            "screenshot_signature": "same-screen",
        },
        {
            "action": "wait",
            "params": {"seconds": 1.5},
            "screenshot_signature": "same-screen",
        },
    ]

    analysis = autonomous_loop._analyze_stuck_state(history, {"signature": "same-screen"})

    assert analysis["active"] is True
    assert analysis["signals"][0]["signal"] == "loading_no_response"
    assert analysis["recovery_strategy"]["signal"] == "loading_no_response"
    assert analysis["recovery_action"]["action"] == "wait"


def test_analyze_stuck_state_flags_repeated_action_without_progress():
    history = [
        {
            "action": "click",
            "params": {"x": 200, "y": 300},
            "screenshot_signature": "same-screen",
        },
        {
            "action": "click",
            "params": {"x": 200, "y": 300},
            "screenshot_signature": "same-screen",
        },
        {
            "action": "wait",
            "params": {"seconds": 1.5},
            "screenshot_signature": "same-screen",
            "recovery_strategy": {"signal": "repeated_action_no_progress", "attempt": 1},
        },
    ]

    analysis = autonomous_loop._analyze_stuck_state(history, {"signature": "same-screen"})

    assert analysis["active"] is True
    assert analysis["signals"][0]["signal"] == "repeated_action_no_progress"
    assert "Choose a materially different action" in analysis["planner_guidance"]
    assert analysis["avoid_action_fingerprint"].startswith("click:")


def test_analyze_stuck_state_stops_after_repeated_target_acquisition_failures():
    history = []
    for _ in range(4):
        history.append(
            {
                "action": "type",
                "params": {"text": "Empyralis"},
                "success": False,
                "needs_replan": True,
                "result_payload": {
                    "error": "Could not establish focus before typing.",
                },
                "screenshot_signature": "same-screen",
            }
        )

    analysis = autonomous_loop._analyze_stuck_state(history, {"signature": "same-screen"})

    assert analysis["active"] is True
    assert analysis["should_stop"] is True
    assert analysis["status"] == "stuck_target_acquisition"


def test_analyze_stuck_state_stops_after_visual_stall_exhausts_recovery():
    history = [
        {
            "action": "wait",
            "params": {"seconds": 0.8},
            "screenshot_signature": "same-screen",
            "recovery_strategy": {"signal": "visual_stall", "attempt": 1},
        },
        {
            "action": "launch_app",
            "params": {"target": "Safari"},
            "screenshot_signature": "same-screen",
        },
        {
            "action": "key",
            "params": {"key": "tab"},
            "screenshot_signature": "same-screen",
        },
        {
            "action": "click",
            "params": {"x": 200, "y": 300},
            "screenshot_signature": "same-screen",
        },
        {
            "action": "type",
            "params": {"text": "Empyralis"},
            "success": True,
            "screenshot_signature": "same-screen",
        },
    ]

    analysis = autonomous_loop._analyze_stuck_state(history, {"signature": "same-screen"})

    assert analysis["active"] is True
    assert analysis["should_stop"] is True
    assert analysis["status"] == "stuck_visual_stall"


def test_planned_action_policy_requires_approval_for_credential_entry():
    action = {
        "action": "type",
        "text": "super-secret-password",
        "target_description": "password field",
        "reason": "Sign in to continue.",
    }

    result = autonomous_loop._evaluate_planned_action_policy(action)

    assert result["execution_decision"] == "require_confirmation"
    assert "credential_entry" in result["dangerous_action_classes"]


def test_low_confidence_dangerous_action_forces_approval_even_if_class_is_allowlisted():
    action = {
        "action": "type",
        "text": "super-secret-password",
        "target_description": "password field",
        "reason": "Sign in to continue.",
        "confidence": 0.31,
        "certainty": "low",
    }

    with patch.dict(os.environ, {"EMPYRALIS_AUTONOMOUS_ALLOW_DANGEROUS_CLASSES": "credential_entry"}):
        result = autonomous_loop._evaluate_planned_action_policy(action)

    assert result["execution_decision"] == "require_confirmation"
    assert result["reason"] == "low_confidence_dangerous_action"
    assert result["planner_confidence"] == 0.31


def test_capture_readiness_snapshot_detects_ready_browser_search_surface():
    screenshot = {"width": 1440, "height": 900, "signature": "sig"}
    history = [{"action": "launch_app", "params": {"target": "https://www.google.com"}}]

    with patch.object(
        autonomous_loop.supervisor_client,
        "ocr",
        return_value={"text": "Google Search Images Gmail"},
    ):
        readiness = autonomous_loop._capture_readiness_snapshot("open google and search for Empyralis AI", history, screenshot)

    assert readiness["context"] == "browser"
    assert readiness["state"] == "input_ready"
    assert "Browser input looks ready" in readiness["summary"]


def test_low_confidence_click_without_grounding_requests_replan():
    action = {
        "action": "click",
        "x": 640,
        "y": 320,
        "reason": "Maybe click something around here.",
        "confidence": 0.22,
        "certainty": "low",
    }

    result = autonomous_loop._execute_action(action, width=1440, height=900)

    assert result["success"] is False
    assert result["needs_replan"] is True
    assert "Low-confidence click did not include confirm_text or target_text" in result["result"]["error"]
