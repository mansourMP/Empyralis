import json
import hashlib
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode


def ensure_trailing_slashless(url: str) -> str:
    return str(url or "").rstrip("/")


class RateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: int = 1):
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class ApiRequestError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RuntimeInterruptController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: Optional[Dict[str, Any]] = None
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._active_process_run_id: Optional[str] = None
        self._active_supervisor_interrupt_fn: Optional[Callable[[], Any]] = None
        self._active_supervisor_run_id: Optional[str] = None
        self._active_supervisor_request_id: Optional[str] = None

    def _applies_to_run(self, snapshot: Optional[Dict[str, Any]], run_id: Optional[str]) -> bool:
        if not isinstance(snapshot, dict):
            return False
        scope = str(snapshot.get("scope") or "run").strip().lower() or "run"
        if scope == "machine":
            return True
        target_run_id = str(snapshot.get("run_id") or "").strip()
        current_run_id = str(run_id or "").strip()
        return bool(target_run_id) and bool(current_run_id) and target_run_id == current_run_id

    def _terminate_process(self, process: Optional[subprocess.Popen[str]]) -> None:
        if process is None:
            return
        try:
            if process.poll() is not None:
                return
        except Exception:
            return
        try:
            if hasattr(os, "killpg"):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        except Exception:
            try:
                process.kill()
            except Exception:
                return

    def request_interrupt(
        self,
        *,
        scope: str,
        event_type: str,
        reason: Optional[str] = None,
        run_id: Optional[str] = None,
        machine_id: Optional[str] = None,
        requested_at: Optional[str] = None,
        sequence: Optional[int] = None,
    ) -> Dict[str, Any]:
        snapshot = {
            "interrupt_requested": True,
            "scope": str(scope or "run").strip().lower() or "run",
            "event_type": str(event_type or "hard_kill").strip().lower() or "hard_kill",
            "reason": str(reason or "").strip() or None,
            "run_id": str(run_id or "").strip() or None,
            "machine_id": str(machine_id or "").strip() or None,
            "requested_at": str(requested_at or "").strip() or None,
            "sequence": int(sequence) if isinstance(sequence, (int, float)) else None,
        }
        process: Optional[subprocess.Popen[str]] = None
        supervisor_interrupt_fn: Optional[Callable[[], Any]] = None
        with self._lock:
            self._snapshot = snapshot
            if self._applies_to_run(snapshot, self._active_process_run_id):
                process = self._active_process
            if self._applies_to_run(snapshot, self._active_supervisor_run_id):
                supervisor_interrupt_fn = self._active_supervisor_interrupt_fn
        self._terminate_process(process)
        if callable(supervisor_interrupt_fn):
            try:
                supervisor_interrupt_fn()
            except Exception:
                pass
        return dict(snapshot)

    def interrupt_snapshot(self, *, run_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            snapshot = dict(self._snapshot) if self._applies_to_run(self._snapshot, run_id) else {}
        return snapshot

    def register_process(self, run_id: str, process: subprocess.Popen[str]) -> None:
        snapshot: Dict[str, Any] = {}
        with self._lock:
            self._active_process = process
            self._active_process_run_id = str(run_id or "").strip() or None
            if self._applies_to_run(self._snapshot, self._active_process_run_id):
                snapshot = dict(self._snapshot or {})
        if snapshot:
            self._terminate_process(process)

    def clear_process(self, process: Optional[subprocess.Popen[str]]) -> None:
        if process is None:
            return
        with self._lock:
            if self._active_process is process:
                self._active_process = None
                self._active_process_run_id = None

    def register_supervisor_execution(
        self,
        *,
        run_id: str,
        request_id: str,
        interrupt_fn: Callable[[], Any],
    ) -> None:
        snapshot: Dict[str, Any] = {}
        with self._lock:
            self._active_supervisor_run_id = str(run_id or "").strip() or None
            self._active_supervisor_request_id = str(request_id or "").strip() or None
            self._active_supervisor_interrupt_fn = interrupt_fn
            if self._applies_to_run(self._snapshot, self._active_supervisor_run_id):
                snapshot = dict(self._snapshot or {})
        if snapshot:
            try:
                interrupt_fn()
            except Exception:
                pass

    def clear_supervisor_execution(self, *, request_id: Optional[str] = None) -> None:
        target_request_id = str(request_id or "").strip()
        with self._lock:
            active_request_id = str(self._active_supervisor_request_id or "").strip()
            if target_request_id and active_request_id and target_request_id != active_request_id:
                return
            self._active_supervisor_interrupt_fn = None
            self._active_supervisor_run_id = None
            self._active_supervisor_request_id = None

    def clear_run_interrupt(self, run_id: Optional[str]) -> None:
        target_run_id = str(run_id or "").strip()
        if not target_run_id:
            return
        with self._lock:
            if self._applies_to_run(self._snapshot, target_run_id):
                scope = str((self._snapshot or {}).get("scope") or "run").strip().lower() or "run"
                if scope == "run":
                    self._snapshot = None

    def clear_machine_interrupt(self, machine_id: Optional[str] = None) -> None:
        target_machine_id = str(machine_id or "").strip()
        with self._lock:
            scope = str((self._snapshot or {}).get("scope") or "").strip().lower()
            if scope != "machine":
                return
            snapshot_machine_id = str((self._snapshot or {}).get("machine_id") or "").strip()
            if target_machine_id and snapshot_machine_id and snapshot_machine_id != target_machine_id:
                return
            self._snapshot = None

    def has_active_process(self) -> bool:
        with self._lock:
            return self._active_process is not None

    def has_active_supervisor_execution(self) -> bool:
        with self._lock:
            return self._active_supervisor_interrupt_fn is not None


class RuntimeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        enrollment_token: str = "",
        timeout_seconds: int = 20,
        session_root: Optional[Any] = None,
    ):
        self.base_url = ensure_trailing_slashless(base_url)
        self.api_key = api_key
        self.enrollment_token = str(enrollment_token or "").strip()
        self.timeout_seconds = timeout_seconds
        self.runtime_session_token: Optional[str] = None
        self.runtime_instance_id: Optional[str] = None
        self._registration: Optional[Dict[str, Any]] = None
        if session_root is None:
            env_session_root = str(os.getenv("ORION_RUNTIME_SESSION_ROOT") or "").strip()
            if env_session_root:
                session_root = Path(env_session_root)
            else:
                session_root = Path(__file__).resolve().parents[1] / ".orion-stack" / "runtime_sessions"
        self.session_root = Path(session_root).expanduser()

    def _session_store_path(self, runtime_id: str) -> Path:
        safe_runtime_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(runtime_id or "").strip()).strip("-.") or "runtime"
        return self.session_root / f"{safe_runtime_id}.json"

    def _remember_registration(
        self,
        runtime_id: str,
        *,
        runtime_type: str = "local",
        display_name: Optional[str] = None,
        platform: Optional[str] = None,
        policy_mode: str = "local_default",
        capabilities: Optional[list[str]] = None,
        execution_targets: Optional[list[str]] = None,
        instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        effective_instance_id = str(instance_id or self.runtime_instance_id or runtime_id).strip() or runtime_id
        registration = {
            "runtime_id": runtime_id,
            "runtime_type": runtime_type,
            "display_name": display_name,
            "platform": platform,
            "policy_mode": policy_mode,
            "capabilities": list(capabilities or []),
            "execution_targets": list(execution_targets or ["local"]),
            "instance_id": effective_instance_id,
        }
        self.runtime_instance_id = effective_instance_id
        self._registration = registration
        return registration

    def _persist_runtime_session(self, runtime_id: str) -> None:
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            return
        session_token = str(self.runtime_session_token or "").strip()
        instance_id = str(self.runtime_instance_id or "").strip()
        if not session_token or not instance_id:
            self.clear_runtime_session(runtime_token)
            return
        payload: Dict[str, Any] = {
            "runtime_id": runtime_token,
            "session_token": session_token,
            "instance_id": instance_id,
        }
        if isinstance(self._registration, dict):
            payload["registration"] = dict(self._registration)
        target = self._session_store_path(runtime_token)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        try:
            os.chmod(tmp_path, 0o600)
        except Exception:
            pass
        tmp_path.replace(target)

    def load_runtime_session(self, runtime_id: str) -> bool:
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            return False
        target = self._session_store_path(runtime_token)
        if not target.exists():
            return False
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        if str(payload.get("runtime_id") or runtime_token).strip() != runtime_token:
            return False
        session_token = str(payload.get("session_token") or "").strip()
        instance_id = str(payload.get("instance_id") or "").strip()
        if not session_token or not instance_id:
            return False
        self.runtime_session_token = session_token
        self.runtime_instance_id = instance_id
        registration = payload.get("registration") if isinstance(payload.get("registration"), dict) else None
        if isinstance(registration, dict):
            restored = dict(registration)
            restored["runtime_id"] = runtime_token
            restored["instance_id"] = str(restored.get("instance_id") or instance_id).strip() or instance_id
            self._registration = restored
        elif not isinstance(self._registration, dict):
            self._registration = {
                "runtime_id": runtime_token,
                "runtime_type": "local",
                "display_name": None,
                "platform": None,
                "policy_mode": "local_default",
                "capabilities": [],
                "execution_targets": ["local"],
                "instance_id": instance_id,
            }
        return True

    def clear_runtime_session(self, runtime_id: str) -> None:
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            return
        target = self._session_store_path(runtime_token)
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            return

    def _capability_digest(self, capabilities: Optional[list[str]]) -> Optional[str]:
        items = [str(item).strip() for item in (capabilities or []) if str(item).strip()]
        if not items:
            return None
        return hashlib.sha256("\n".join(sorted(set(items))).encode("utf-8")).hexdigest()[:16]

    def _retry_with_reregister(self, runtime_id: str, fn):
        try:
            return fn()
        except ApiRequestError as exc:
            if exc.status_code not in {401, 404, 409}:
                raise
            registration = self._registration if isinstance(self._registration, dict) else None
            if not registration or str(registration.get("runtime_id") or "").strip() != runtime_id:
                raise
            self.runtime_session_token = None
            self.clear_runtime_session(runtime_id)
            self._bootstrap_registration(registration)
            return fn()

    def _auth_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if str(self.api_key or "").strip():
            headers["X-API-Key"] = str(self.api_key or "").strip()
        return headers

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None
        headers = self._auth_headers()
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url=url, data=body, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8") if response else ""
                if not text:
                    return {}
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8")
            except Exception:
                raw = ""
            detail = raw or str(exc)
            retry_after_header = exc.headers.get("Retry-After") if exc.headers is not None else None
            retry_after_seconds = 1
            if retry_after_header:
                try:
                    retry_after_seconds = max(1, int(str(retry_after_header).strip()))
                except Exception:
                    retry_after_seconds = 1
            parsed_detail: Optional[Dict[str, Any]] = None
            try:
                parsed_candidate = json.loads(detail) if detail else None
                if isinstance(parsed_candidate, dict):
                    parsed_detail = parsed_candidate
            except Exception:
                parsed_detail = None
            if parsed_detail and isinstance(parsed_detail.get("retry_after_seconds"), (int, float)):
                retry_after_seconds = max(1, int(parsed_detail.get("retry_after_seconds")))
            if exc.code == 429:
                raise RateLimitError(f"{method} {path} failed: {detail}", retry_after_seconds=retry_after_seconds) from exc
            raise ApiRequestError(f"{method} {path} failed: {detail}", status_code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{method} {path} failed: {exc}") from exc

    def _open_stream(self, path: str, query: Optional[Dict[str, Any]] = None):
        url = f"{self.base_url}{path}"
        if isinstance(query, dict):
            normalized_query = {
                key: value
                for key, value in query.items()
                if value is not None and str(value).strip() != ""
            }
            if normalized_query:
                url = f"{url}?{urlencode(normalized_query, doseq=True)}"
        request = urllib.request.Request(
            url=url,
            method="GET",
            headers={
                **self._auth_headers(),
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            },
        )
        try:
            return urllib.request.urlopen(request, timeout=max(65, int(self.timeout_seconds or 20)))
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8")
            except Exception:
                raw = ""
            raise ApiRequestError(raw or str(exc), status_code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GET {path} failed: {exc}") from exc

    def _request_with_fallback(
        self,
        method: str,
        primary_path: str,
        fallback_path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            return self._request(method, primary_path, payload)
        except ApiRequestError as exc:
            if exc.status_code not in {404, 405}:
                raise
            return self._request(method, fallback_path, payload)

    def register_runtime(
        self,
        runtime_id: str,
        *,
        runtime_type: str = "local",
        display_name: Optional[str] = None,
        platform: Optional[str] = None,
        policy_mode: str = "local_default",
        capabilities: Optional[list[str]] = None,
        execution_targets: Optional[list[str]] = None,
        instance_id: Optional[str] = None,
        permission_probe: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not str(self.api_key or "").strip():
            raise RuntimeError("Runtime API key is required for direct runtime registration.")
        registration = self._remember_registration(
            runtime_id,
            runtime_type=runtime_type,
            display_name=display_name,
            platform=platform,
            policy_mode=policy_mode,
            capabilities=capabilities,
            execution_targets=execution_targets,
            instance_id=instance_id,
        )
        effective_instance_id = str(registration.get("instance_id") or runtime_id).strip() or runtime_id
        payload: Dict[str, Any] = {
            "runtime_type": runtime_type,
            "display_name": display_name,
            "platform": platform,
            "policy_mode": policy_mode,
            "capabilities": capabilities or [],
            "execution_targets": execution_targets or ["local"],
            "instance_id": effective_instance_id,
            "capability_digest": self._capability_digest(capabilities),
            "note": "local_worker_boot",
        }
        if self.enrollment_token:
            payload["enrollment_token"] = self.enrollment_token
        if isinstance(permission_probe, dict) and permission_probe:
            payload["permission_probe"] = permission_probe
        try:
            result = self._request("POST", f"/runtime/runtimes/{runtime_id}/register", payload)
            self.runtime_session_token = str(result.get("session_token") or "").strip() or None
            self.runtime_instance_id = str(result.get("instance_id") or effective_instance_id).strip() or effective_instance_id
            registration["instance_id"] = self.runtime_instance_id
            self._registration = registration
            self._persist_runtime_session(runtime_id)
            return result
        except ApiRequestError as exc:
            if exc.status_code not in {404, 405}:
                raise
            self.heartbeat_worker(runtime_id, None, "runtime_registered")
            return {"ok": True, "runtime_id": runtime_id, "legacy": True}

    def bootstrap_companion_runtime(
        self,
        runtime_id: str,
        *,
        runtime_type: str = "local_companion",
        display_name: Optional[str] = None,
        platform: Optional[str] = None,
        policy_mode: str = "local_default",
        capabilities: Optional[list[str]] = None,
        execution_targets: Optional[list[str]] = None,
        instance_id: Optional[str] = None,
        permission_probe: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not self.enrollment_token:
            raise RuntimeError("Machine enrollment token is required for companion bootstrap.")
        registration = self._remember_registration(
            runtime_id,
            runtime_type=runtime_type,
            display_name=display_name,
            platform=platform,
            policy_mode=policy_mode,
            capabilities=capabilities,
            execution_targets=execution_targets,
            instance_id=instance_id,
        )
        effective_instance_id = str(registration.get("instance_id") or runtime_id).strip() or runtime_id
        payload: Dict[str, Any] = {
            "enrollment_token": self.enrollment_token,
            "runtime_type": runtime_type,
            "display_name": display_name,
            "platform": platform,
            "policy_mode": policy_mode,
            "capabilities": capabilities or [],
            "execution_targets": execution_targets or ["local_companion"],
            "instance_id": effective_instance_id,
            "capability_digest": self._capability_digest(capabilities),
            "note": "companion_platform_relay_boot",
        }
        if isinstance(permission_probe, dict) and permission_probe:
            payload["permission_probe"] = permission_probe
        result = self._request("POST", f"/runtime/companions/{runtime_id}/bootstrap", payload)
        self.runtime_session_token = str(result.get("session_token") or "").strip() or None
        self.runtime_instance_id = str(result.get("instance_id") or effective_instance_id).strip() or effective_instance_id
        registration["instance_id"] = self.runtime_instance_id
        self._registration = registration
        self._persist_runtime_session(runtime_id)
        return result

    def _bootstrap_registration(self, registration: Dict[str, Any]) -> Dict[str, Any]:
        runtime_id = str(registration.get("runtime_id") or "").strip()
        if not runtime_id:
            raise RuntimeError("runtime_id is required for runtime bootstrap.")
        execution_targets = list(registration.get("execution_targets") or [])
        runtime_type = str(registration.get("runtime_type") or "local").strip() or "local"
        permission_probe = (
            dict(registration.get("permission_probe"))
            if isinstance(registration.get("permission_probe"), dict)
            else None
        )
        if self.enrollment_token and (
            runtime_type == "local_companion" or "local_companion" in execution_targets
        ):
            return self.bootstrap_companion_runtime(
                runtime_id,
                runtime_type=runtime_type,
                display_name=str(registration.get("display_name") or "") or None,
                platform=str(registration.get("platform") or "") or None,
                policy_mode=str(registration.get("policy_mode") or "local_default"),
                capabilities=list(registration.get("capabilities") or []),
                execution_targets=execution_targets or ["local_companion"],
                instance_id=str(registration.get("instance_id") or "") or None,
                permission_probe=permission_probe,
            )
        return self.register_runtime(
            runtime_id,
            runtime_type=runtime_type,
            display_name=str(registration.get("display_name") or "") or None,
            platform=str(registration.get("platform") or "") or None,
            policy_mode=str(registration.get("policy_mode") or "local_default"),
            capabilities=list(registration.get("capabilities") or []),
            execution_targets=execution_targets or ["local"],
            instance_id=str(registration.get("instance_id") or "") or None,
            permission_probe=permission_probe,
        )

    def bootstrap_runtime_session(
        self,
        runtime_id: str,
        *,
        runtime_type: str = "local",
        display_name: Optional[str] = None,
        platform: Optional[str] = None,
        policy_mode: str = "local_default",
        capabilities: Optional[list[str]] = None,
        execution_targets: Optional[list[str]] = None,
        instance_id: Optional[str] = None,
        permission_probe: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        registration = self._remember_registration(
            runtime_id,
            runtime_type=runtime_type,
            display_name=display_name,
            platform=platform,
            policy_mode=policy_mode,
            capabilities=capabilities,
            execution_targets=execution_targets,
            instance_id=instance_id,
        )
        self.load_runtime_session(runtime_id)
        if self.runtime_session_token:
            try:
                self.heartbeat_worker(runtime_id, None, "runtime_session_resume", permission_probe=permission_probe)
                return {
                    "ok": True,
                    "runtime_id": runtime_id,
                    "instance_id": self.runtime_instance_id,
                    "session_token": self.runtime_session_token,
                    "resumed": True,
                }
            except ApiRequestError as exc:
                if exc.status_code not in {401, 404, 409}:
                    raise
                self.runtime_session_token = None
                self.clear_runtime_session(runtime_id)
        if isinstance(permission_probe, dict) and permission_probe:
            registration = {
                **registration,
                "permission_probe": dict(permission_probe),
            }
        result = self._bootstrap_registration(registration)
        if self.runtime_session_token or bool(result.get("legacy")):
            result["resumed"] = False
            return result
        raise RuntimeError("runtime session token missing after runtime registration")

    def claim_run(self, worker_id: str) -> Dict[str, Any]:
        response = self._retry_with_reregister(
            worker_id,
            lambda: self._request_with_fallback(
                "POST",
                "/runtime/tasks/claim",
                "/local/runs/claim",
                {
                    "runtime_id": worker_id,
                    "session_token": self.runtime_session_token,
                    "instance_id": self.runtime_instance_id,
                    "execution_target": "local",
                },
            ),
        )
        task = response.get("task") if isinstance(response.get("task"), dict) else None
        if task is not None and "run" not in response:
            run = task.get("run") if isinstance(task.get("run"), dict) else None
            if isinstance(run, dict):
                response["run"] = run
        return response

    def heartbeat_run(self, run_id: str, worker_id: str, note: str, event: Optional[Dict[str, Any]] = None):
        self._retry_with_reregister(
            worker_id,
            lambda: self._request_with_fallback(
                "POST",
                f"/runtime/tasks/{run_id}/heartbeat",
                f"/local/runs/{run_id}/heartbeat",
                {
                    "runtime_id": worker_id,
                    "session_token": self.runtime_session_token,
                    "instance_id": self.runtime_instance_id,
                    "note": note[:300],
                    "event": (dict(event) if isinstance(event, dict) else None),
                },
            ),
        )

    def get_task_control_state(self, run_id: str, worker_id: str) -> Dict[str, Any]:
        return self._retry_with_reregister(
            worker_id,
            lambda: self._request(
                "POST",
                f"/runtime/tasks/{run_id}/control-state",
                {
                    "runtime_id": worker_id,
                    "session_token": self.runtime_session_token,
                    "instance_id": self.runtime_instance_id,
                },
            ),
        )

    def iter_control_events(self, runtime_id: str, *, since_sequence: int = 0):
        runtime_token = str(runtime_id or "").strip()
        if not runtime_token:
            raise RuntimeError("runtime_id is required for the control stream.")
        stream = self._open_stream(
            f"/runtime/runtimes/{runtime_token}/control/stream",
            query={
                "session_token": self.runtime_session_token,
                "instance_id": self.runtime_instance_id,
                "since_sequence": max(0, int(since_sequence or 0)),
            },
        )
        event_name = "message"
        event_id: Optional[str] = None
        data_lines: list[str] = []
        with stream as response:
            while True:
                raw_line = response.readline()
                if not raw_line:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        try:
                            parsed = json.loads(payload) if payload else {}
                        except Exception:
                            parsed = {"raw": payload}
                        yield {
                            "event": event_name,
                            "id": event_id,
                            "data": parsed if isinstance(parsed, dict) else {"raw": payload},
                        }
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        try:
                            parsed = json.loads(payload) if payload else {}
                        except Exception:
                            parsed = {"raw": payload}
                        yield {
                            "event": event_name,
                            "id": event_id,
                            "data": parsed if isinstance(parsed, dict) else {"raw": payload},
                        }
                    event_name = "message"
                    event_id = None
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field == "event":
                    event_name = value.strip() or "message"
                elif field == "id":
                    event_id = value.strip() or None
                elif field == "data":
                    data_lines.append(value)

    def start_control_stream_listener(
        self,
        runtime_id: str,
        *,
        on_event,
        stop_event: threading.Event,
        reconnect_delay_seconds: float = 1.0,
    ) -> threading.Thread:
        runtime_token = str(runtime_id or "").strip()

        def _run() -> None:
            since_sequence = 0
            while not stop_event.is_set():
                try:
                    for event in self.iter_control_events(runtime_token, since_sequence=since_sequence):
                        if stop_event.is_set():
                            break
                        payload = event.get("data") if isinstance(event.get("data"), dict) else {}
                        sequence_value = payload.get("sequence")
                        if isinstance(sequence_value, (int, float)):
                            since_sequence = max(since_sequence, int(sequence_value))
                        on_event(event)
                except ApiRequestError as exc:
                    if exc.status_code not in {401, 404, 409}:
                        time.sleep(max(0.25, reconnect_delay_seconds))
                    else:
                        time.sleep(max(0.25, reconnect_delay_seconds))
                except Exception:
                    time.sleep(max(0.25, reconnect_delay_seconds))

        thread = threading.Thread(
            target=_run,
            name=f"runtime-control-listener-{runtime_token}",
            daemon=True,
        )
        thread.start()
        return thread

    def complete_run(
        self,
        run_id: str,
        worker_id: str,
        result_text: str,
        result_data: Optional[Dict[str, Any]] = None,
        usage_masked: Optional[Dict[str, Any]] = None,
    ):
        def _payload() -> Dict[str, Any]:
            payload: Dict[str, Any] = {"worker_id": worker_id, "result_text": result_text}
            if isinstance(result_data, dict):
                payload["result_data"] = result_data
            if isinstance(usage_masked, dict):
                payload["usage_masked"] = usage_masked
            payload["runtime_id"] = worker_id
            payload["session_token"] = self.runtime_session_token
            payload["instance_id"] = self.runtime_instance_id
            return payload

        self._retry_with_reregister(
            worker_id,
            lambda: self._request_with_fallback(
                "POST",
                f"/runtime/tasks/{run_id}/complete",
                f"/local/runs/{run_id}/complete",
                _payload(),
            ),
        )

    def pause_run(
        self,
        run_id: str,
        worker_id: str,
        result_text: str,
        *,
        result_data: Optional[Dict[str, Any]] = None,
        browser_checkpoint: Optional[Dict[str, Any]] = None,
        wait_reason: Optional[str] = None,
    ):
        def _payload() -> Dict[str, Any]:
            payload: Dict[str, Any] = {
                "runtime_id": worker_id,
                "session_token": self.runtime_session_token,
                "instance_id": self.runtime_instance_id,
                "result_text": result_text,
            }
            if isinstance(result_data, dict):
                payload["result_data"] = result_data
            if isinstance(browser_checkpoint, dict):
                payload["browser_checkpoint"] = browser_checkpoint
            if str(wait_reason or "").strip():
                payload["wait_reason"] = str(wait_reason).strip()
            return payload
        self._retry_with_reregister(
            worker_id,
            lambda: self._request(
                "POST",
                f"/runtime/tasks/{run_id}/pause",
                _payload(),
            ),
        )

    def fail_run(self, run_id: str, worker_id: str, error_message: str):
        def _payload() -> Dict[str, Any]:
            return {
                "runtime_id": worker_id,
                "session_token": self.runtime_session_token,
                "instance_id": self.runtime_instance_id,
                "error": error_message[:1000],
            }

        self._retry_with_reregister(
            worker_id,
            lambda: self._request_with_fallback(
                "POST",
                f"/runtime/tasks/{run_id}/fail",
                f"/local/runs/{run_id}/fail",
                _payload(),
            ),
        )

    def heartbeat_worker(
        self,
        worker_id: str,
        current_run_id: Optional[str],
        note: str,
        *,
        permission_probe: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        def _payload() -> Dict[str, Any]:
            payload: Dict[str, Any] = {"note": note[:240]}
            if current_run_id:
                payload["current_run_id"] = current_run_id
            payload["session_token"] = self.runtime_session_token
            payload["instance_id"] = self.runtime_instance_id
            if isinstance(permission_probe, dict) and permission_probe:
                payload["permission_probe"] = permission_probe
            return payload

        self._retry_with_reregister(
            worker_id,
            lambda: self._request_with_fallback(
                "POST",
                f"/runtime/runtimes/{worker_id}/heartbeat",
                f"/local/workers/{worker_id}/heartbeat",
                _payload(),
            ),
        )
