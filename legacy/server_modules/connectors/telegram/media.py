from __future__ import annotations

import mimetypes
import os
import re
import ssl
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib import request as urlrequest

import certifi

from server_modules import rust_runtime_kernel_client


def telegram_safe_path_token(value: Any) -> str:
    token = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip())
    return token.strip("._-") or "unknown"


class TelegramMediaService:
    def __init__(
        self,
        *,
        media_dir: Path,
        media_enabled: bool,
        media_max_items: int,
        media_max_bytes: int,
        media_include_in_goal: bool,
        telegram_api_request: Callable[..., Dict[str, Any]],
    ) -> None:
        self.media_dir = Path(media_dir)
        self.media_enabled = bool(media_enabled)
        self.media_max_items = max(1, int(media_max_items or 1))
        self.media_max_bytes = max(1024, int(media_max_bytes or 1024))
        self.media_include_in_goal = bool(media_include_in_goal)
        self.telegram_api_request = telegram_api_request

    def _enforce_media_file_write(self, *, remote_file_path: str, dest_path: Path, max_bytes: int) -> None:
        payload = {
            "remote_suffix": Path(str(remote_file_path or "")).suffix.strip().lower()[:16],
            "dest_path": str(dest_path),
            "max_bytes": max(0, int(max_bytes or 0)),
        }
        try:
            decision = rust_runtime_kernel_client.runtime_state_store_decision(
                operation="write_telegram_media_file",
                state_class="telegram_media_files",
                actor_id="system",
                status="active",
                payload=payload,
                payload_bytes=int(payload["max_bytes"]),
                workspace_access=True,
                owner_access=True,
            )
            rust_runtime_kernel_client.enforce_kernel_decision(
                "runtime-state-store-decision",
                decision,
            )
            next_action = str(decision.get("next_action") or "").strip()
            if next_action != "write_telegram_media_file":
                raise RuntimeError("unexpected_next_action")
        except rust_runtime_kernel_client.RustKernelDecisionError as exc:
            raise RuntimeError(exc.reason) from exc

    def extract_message(self, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(update, dict):
            return None
        for key in (
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "business_message",
            "edited_business_message",
        ):
            candidate = update.get(key)
            if isinstance(candidate, dict):
                text = str(candidate.get("text") or candidate.get("caption") or "").strip()
                reply_to = candidate.get("reply_to_message") if isinstance(candidate.get("reply_to_message"), dict) else {}
                attachments: List[Dict[str, Any]] = []
                photos = candidate.get("photo") if isinstance(candidate.get("photo"), list) else []
                if photos:
                    best_photo: Optional[Dict[str, Any]] = None
                    for photo in photos:
                        if not isinstance(photo, dict):
                            continue
                        if best_photo is None:
                            best_photo = photo
                            continue
                        best_size = int(best_photo.get("file_size") or 0)
                        best_area = int(best_photo.get("width") or 0) * int(best_photo.get("height") or 0)
                        cur_size = int(photo.get("file_size") or 0)
                        cur_area = int(photo.get("width") or 0) * int(photo.get("height") or 0)
                        if cur_size > best_size or cur_area > best_area:
                            best_photo = photo
                    if isinstance(best_photo, dict):
                        file_id = str(best_photo.get("file_id") or "").strip()
                        if file_id:
                            attachments.append(
                                {
                                    "kind": "photo",
                                    "file_id": file_id,
                                    "file_unique_id": str(best_photo.get("file_unique_id") or "").strip(),
                                    "file_size": int(best_photo.get("file_size") or 0),
                                    "mime_type": "image/jpeg",
                                    "width": int(best_photo.get("width") or 0),
                                    "height": int(best_photo.get("height") or 0),
                                }
                            )
                document = candidate.get("document") if isinstance(candidate.get("document"), dict) else None
                if isinstance(document, dict):
                    mime_type = str(document.get("mime_type") or "").strip().lower()
                    if mime_type.startswith("image/"):
                        file_id = str(document.get("file_id") or "").strip()
                        if file_id:
                            attachments.append(
                                {
                                    "kind": "document_image",
                                    "file_id": file_id,
                                    "file_unique_id": str(document.get("file_unique_id") or "").strip(),
                                    "file_size": int(document.get("file_size") or 0),
                                    "mime_type": mime_type or "application/octet-stream",
                                    "file_name": str(document.get("file_name") or "").strip(),
                                }
                            )
                return {
                    "text": text,
                    "chat": candidate.get("chat") if isinstance(candidate.get("chat"), dict) else {},
                    "from": candidate.get("from") if isinstance(candidate.get("from"), dict) else {},
                    "message_id": candidate.get("message_id"),
                    "reply_to_message_id": reply_to.get("message_id"),
                    "date": candidate.get("date"),
                    "kind": key,
                    "attachments": attachments,
                }
        return None

    def extension_from_attachment(self, attachment: Dict[str, Any], remote_file_path: str) -> str:
        suffix = Path(str(remote_file_path or "")).suffix.strip().lower()
        if suffix:
            return suffix[:12]
        file_name = str(attachment.get("file_name") or "").strip()
        if file_name:
            name_suffix = Path(file_name).suffix.strip().lower()
            if name_suffix:
                return name_suffix[:12]
        mime_type = str(attachment.get("mime_type") or "").strip().lower()
        guessed = mimetypes.guess_extension(mime_type) if mime_type else None
        if guessed:
            return str(guessed).strip().lower()[:12]
        return ".bin"

    def download_file(self, bot_token: str, remote_file_path: str, dest_path: Path, max_bytes: int) -> int:
        self._enforce_media_file_write(
            remote_file_path=remote_file_path,
            dest_path=dest_path,
            max_bytes=max_bytes,
        )
        file_url = f"https://api.telegram.org/file/bot{bot_token}/{remote_file_path}"
        req = urlrequest.Request(file_url, method="GET")
        context = ssl.create_default_context(cafile=certifi.where())
        total = 0
        with urlrequest.urlopen(req, timeout=20, context=context) as resp:
            with dest_path.open("wb") as handle:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError(f"Attachment exceeds max size ({max_bytes} bytes).")
                    handle.write(chunk)
        return total

    def store_attachments(
        self,
        *,
        bot_token: str,
        workspace_id: str,
        chat_id: str,
        update_id: int,
        message_id: str,
        attachments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not self.media_enabled:
            return []
        if not isinstance(attachments, list) or not attachments:
            return []

        workspace_token = telegram_safe_path_token(workspace_id or "default")
        chat_token = telegram_safe_path_token(chat_id or "unknown")
        message_token = telegram_safe_path_token(message_id or str(update_id))
        base_dir = self.media_dir / workspace_token / chat_token
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(base_dir, 0o700)
            except Exception:
                pass
        except Exception:
            return []

        stored: List[Dict[str, Any]] = []
        for idx, attachment in enumerate(attachments[: self.media_max_items], start=1):
            if not isinstance(attachment, dict):
                continue
            file_id = str(attachment.get("file_id") or "").strip()
            if not file_id:
                continue
            try:
                meta = self.telegram_api_request(bot_token, "getFile", params={"file_id": file_id})
                remote_path = str(meta.get("file_path") or "").strip()
                if not remote_path:
                    continue
                ext = self.extension_from_attachment(attachment, remote_path)
                filename = f"{telegram_safe_path_token(update_id)}_{message_token}_{idx}{ext}"
                destination = base_dir / filename
                size_bytes = self.download_file(
                    bot_token=bot_token,
                    remote_file_path=remote_path,
                    dest_path=destination,
                    max_bytes=self.media_max_bytes,
                )
                try:
                    os.chmod(destination, 0o600)
                except Exception:
                    pass
                relative_path = str(destination).replace(str(Path.cwd()) + os.sep, "")
                stored.append(
                    {
                        "kind": str(attachment.get("kind") or "").strip() or "attachment",
                        "mime_type": str(attachment.get("mime_type") or "").strip(),
                        "file_id": file_id,
                        "file_unique_id": str(attachment.get("file_unique_id") or "").strip(),
                        "bytes": int(size_bytes),
                        "path": str(destination),
                        "relative_path": relative_path,
                    }
                )
            except Exception:
                continue
        return stored

    def build_goal_with_attachments(self, goal: str, attachments: List[Dict[str, Any]]) -> str:
        request = str(goal or "").strip()
        if not request:
            request = "Please help with the attached image(s)."
        if not self.media_include_in_goal:
            return request
        if not isinstance(attachments, list) or not attachments:
            return request
        lines = []
        for item in attachments[: self.media_max_items]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("relative_path") or item.get("path") or "").strip()
            if not path:
                continue
            mime = str(item.get("mime_type") or "").strip()
            lines.append(f"- {path}" + (f" ({mime})" if mime else ""))
        if not lines:
            return request
        return (
            f"{request}\n\n"
            "Attached image files were saved locally in the workspace. If needed, inspect them before answering:\n"
            + "\n".join(lines)
        )
