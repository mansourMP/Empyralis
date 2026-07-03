from __future__ import annotations

"""Legacy local-script facade around `server_modules.supervisor_client`.

Production backend code must not import this module to reach the local
supervisor. The canonical cloud path is gateway execution over the paired
gateway websocket.
"""

import logging
from typing import Any, Dict, Optional

import requests

try:
    from . import supervisor_client
except ImportError:
    import supervisor_client  # type: ignore[no-redef]


_CLIPBOARD_FALLBACK_TEXT = ""
_SUPERVISOR_HEALTH_TIMEOUT_SECONDS = 2
logger = logging.getLogger(__name__)


def _check_supervisor_health() -> None:
    try:
        response = requests.get(
            f"{supervisor_client.SUPERVISOR_URL}/health",
            timeout=_SUPERVISOR_HEALTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("empyralis-supervisor is unreachable at import time: %s", exc)


def _supervisor_call(func, *args, **kwargs) -> Dict[str, Any]:
    try:
        result = func(*args, **kwargs)
    except supervisor_client.SupervisorInterruptedError:
        raise
    except RuntimeError as exc:
        message = str(exc or "").strip()
        if message == supervisor_client.SUPERVISOR_UNREACHABLE_MESSAGE:
            return {"error": "Supervisor not running", "success": False}
        return {"error": message or "Supervisor request failed", "success": False}
    if isinstance(result, dict):
        normalized = dict(result)
        normalized.setdefault("success", True)
        return normalized
    return {"result": result, "success": True}


_check_supervisor_health()


def screen_ocr(region: Any = None) -> str:
    # LEGACY:
    # pytesseract = _import_pytesseract()
    # if not shutil.which("tesseract"):
    #     raise RuntimeError("tesseract is not installed on this machine.")
    # image = _capture_screenshot_image(region=region)
    # text = str(pytesseract.image_to_string(image) or "").strip()
    # return text
    result = _supervisor_call(supervisor_client.ocr, monitor="primary", region=region)
    if not result.get("success", False):
        raise RuntimeError(str(result.get("error") or "OCR failed.").strip())
    return str(result.get("text") or "").strip()


def capture_screenshot(monitor: str = "primary", region: Any = None) -> Dict[str, Any]:
    return _supervisor_call(
        supervisor_client.capture_screenshot,
        monitor=monitor,
        region=region,
    )


def computer_control_click(
    x: Any,
    y: Any,
    button: str = "left",
    double: bool = False,
) -> Dict[str, Any]:
    return _supervisor_call(
        supervisor_client.click,
        int(x),
        int(y),
        button=button,
        double=bool(double),
    )


def computer_control_type(text: str, delay_ms: Optional[int] = None) -> Dict[str, Any]:
    return _supervisor_call(
        supervisor_client.type_text,
        str(text or ""),
        delay_ms=delay_ms,
    )


def computer_control_clipboard_read() -> Dict[str, Any]:
    return _supervisor_call(supervisor_client.clipboard_read)


def computer_control_clipboard_write(text: str) -> Dict[str, Any]:
    return _supervisor_call(supervisor_client.clipboard_write, str(text or ""))


def computer_control_list_apps() -> Dict[str, Any]:
    return _supervisor_call(supervisor_client.list_apps)


def computer_control_launch_app(name_or_path: str) -> Dict[str, Any]:
    return _supervisor_call(supervisor_client.launch_app, str(name_or_path or ""))


def mouse_click(x: Any, y: Any) -> str:
    # LEGACY:
    # pyautogui = _import_pyautogui()
    # click_x = int(x)
    # click_y = int(y)
    # pyautogui.click(x=click_x, y=click_y)
    # return f"Clicked at ({click_x}, {click_y})."
    result = computer_control_click(x, y)
    if not result.get("success", False):
        return result  # type: ignore[return-value]
    click_x = int(x)
    click_y = int(y)
    return f"Clicked at ({click_x}, {click_y})."


def click_element_by_text(text: str) -> str:
    target = str(text or "").strip()
    if not target:
        raise RuntimeError("Text is required.")
    result = _supervisor_call(supervisor_client.click, text=target)
    if not result.get("success", False):
        raise RuntimeError(str(result.get("error") or f"Could not find visible text matching '{target}'.").strip())
    click_x = int(result.get("x") or 0)
    click_y = int(result.get("y") or 0)
    return f"Clicked at ({click_x}, {click_y})."


def keyboard_type(text: str) -> str:
    # LEGACY:
    # payload = str(text or "")
    # if not payload:
    #     raise RuntimeError("Text is required.")
    # pyautogui = _import_pyautogui()
    # pyautogui.write(payload, interval=0.01)
    # return f"Typed {len(payload)} characters."
    payload = str(text or "")
    result = computer_control_type(payload)
    if not result.get("success", False):
        return result  # type: ignore[return-value]
    return f"Typed {len(payload)} characters."


def run_applescript(script: str) -> str:
    # LEGACY:
    # if sys.platform != "darwin":
    #     raise RuntimeError("AppleScript is only available on macOS.")
    # payload = str(script or "").strip()
    # if not payload:
    #     raise RuntimeError("AppleScript is required.")
    # completed = subprocess.run(
    #     ["osascript", "-e", payload],
    #     capture_output=True,
    #     text=True,
    #     check=False,
    # )
    # if completed.returncode != 0:
    #     raise RuntimeError(str(completed.stderr or completed.stdout or "AppleScript failed.").strip())
    # return str(completed.stdout or "").strip()
    payload = str(script or "").strip()
    result = _supervisor_call(supervisor_client.run_applescript, payload)
    if not result.get("success", False):
        raise RuntimeError(str(result.get("error") or "AppleScript failed.").strip())
    return str(result.get("output") or "").strip()


def read_clipboard() -> str:
    # LEGACY:
    # global _CLIPBOARD_FALLBACK_TEXT
    # try:
    #     text = str(_import_pyperclip().paste() or "")
    #     if text:
    #         _CLIPBOARD_FALLBACK_TEXT = text
    #     return text
    # except Exception:
    #     try:
    #         text = _clipboard_read_fallback()
    #         _CLIPBOARD_FALLBACK_TEXT = text
    #         return text
    #     except Exception:
    #         return _CLIPBOARD_FALLBACK_TEXT
    global _CLIPBOARD_FALLBACK_TEXT
    result = computer_control_clipboard_read()
    if not result.get("success", False):
        return _CLIPBOARD_FALLBACK_TEXT
    text = str(result.get("text") or "")
    if text:
        _CLIPBOARD_FALLBACK_TEXT = text
    return text


def write_clipboard(text: str) -> str:
    # LEGACY:
    # global _CLIPBOARD_FALLBACK_TEXT
    # payload = str(text or "")
    # _CLIPBOARD_FALLBACK_TEXT = payload
    # try:
    #     _import_pyperclip().copy(payload)
    # except Exception:
    #     try:
    #         _clipboard_write_fallback(payload)
    #     except Exception:
    #         pass
    # return "Clipboard updated."
    global _CLIPBOARD_FALLBACK_TEXT
    payload = str(text or "")
    result = computer_control_clipboard_write(payload)
    if not result.get("success", False):
        _CLIPBOARD_FALLBACK_TEXT = payload
        return "Clipboard updated."
    _CLIPBOARD_FALLBACK_TEXT = payload
    return "Clipboard updated."


def send_notification(title: str, message: str) -> str:
    # LEGACY:
    # resolved_title = str(title or "").strip() or "Empyralist"
    # resolved_message = str(message or "").strip() or "Task completed."
    # if sys.platform == "darwin":
    #     script = (
    #         f'display notification "{_applescript_string(resolved_message)}" '
    #         f'with title "{_applescript_string(resolved_title)}"'
    #     )
    #     subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
    #     return "Notification sent."
    # if shutil.which("notify-send"):
    #     subprocess.run(["notify-send", resolved_title, resolved_message], capture_output=True, text=True, check=False)
    #     return "Notification sent."
    # raise RuntimeError("System notifications are not supported on this machine.")
    resolved_title = str(title or "").strip() or "Empyralist"
    resolved_message = str(message or "").strip() or "Task completed."
    result = _supervisor_call(supervisor_client.notify, resolved_title, resolved_message)
    if not result.get("success", False):
        raise RuntimeError(str(result.get("error") or "Notification failed.").strip())
    return "Notification sent."


def speak_text(text: str, voice: Optional[str] = None) -> str:
    payload = str(text or "").strip()
    if not payload:
        raise RuntimeError("Text is required.")
    # LEGACY:
    # selected_voice = str(voice or "").strip()
    # if sys.platform == "darwin":
    #     command = ["say"]
    #     if selected_voice:
    #         command.extend(["-v", selected_voice])
    #     command.append(payload)
    #     subprocess.run(command, capture_output=True, text=True, check=False)
    #     return "Spoken aloud."
    # if shutil.which("espeak"):
    #     command = ["espeak"]
    #     if selected_voice:
    #         command.extend(["-v", selected_voice])
    #     command.append(payload)
    #     subprocess.run(command, capture_output=True, text=True, check=False)
    #     return "Spoken aloud."
    # raise RuntimeError("System text-to-speech is not supported on this machine.")
    selected_voice = str(voice or "").strip() or None
    result = _supervisor_call(supervisor_client.speak, payload, voice=selected_voice)
    if not result.get("success", False):
        raise RuntimeError(str(result.get("error") or "Text-to-speech failed.").strip())
    return "Spoken aloud."


def list_running_apps() -> List[Dict[str, Any]]:
    # LEGACY:
    # items: List[Dict[str, Any]] = []
    # try:
    #     psutil = _import_psutil()
    #     iterator = psutil.process_iter(attrs=["pid", "name", "exe"])
    # except Exception:
    #     iterator = []
    # ... fallback to `ps -ax -o pid=,comm=`
    result = _supervisor_call(supervisor_client.list_apps)
    if not result.get("success", False):
        return []
    windows = result.get("windows")
    if isinstance(windows, list):
        return windows  # type: ignore[return-value]
    return []


def launch_app(name_or_path: str) -> str:
    # LEGACY:
    # target = str(name_or_path or "").strip()
    # if not target:
    #     raise RuntimeError("App name or path is required.")
    # if sys.platform == "darwin":
    #     if Path(target).expanduser().exists():
    #         subprocess.Popen(["open", str(Path(target).expanduser())])
    #     else:
    #         subprocess.Popen(["open", "-a", target])
    #     return f"Launched {target}."
    # if os.name == "nt":
    #     os.startfile(target)  # type: ignore[attr-defined]
    #     return f"Launched {target}."
    # subprocess.Popen([target])
    # return f"Launched {target}."
    target = str(name_or_path or "").strip()
    result = computer_control_launch_app(target)
    if not result.get("success", False):
        return result  # type: ignore[return-value]
    return f"Launched {target}."
