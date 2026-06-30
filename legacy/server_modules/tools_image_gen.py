from __future__ import annotations

import base64
import importlib.util
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx
from openai import OpenAI

from server_modules import rust_runtime_kernel_client


DEFAULT_OUTPUT_DIR = Path(".orion-stack") / "generated_images"
VALID_MODELS = {"dall-e-3", "dall-e-2", "stable-diffusion"}
VALID_SIZES = {"256x256", "512x512", "1024x1024"}


def _item_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return cleaned.strip("-")[:40] or "image"


def _timestamp_token() -> str:
    return str(int(time.time() * 1000))


def _default_target_dir() -> Path:
    return Path.cwd() / DEFAULT_OUTPUT_DIR


def _target_paths(save_to: Optional[str], *, count: int, prompt: str) -> List[Path]:
    if count <= 0:
        return []
    if save_to:
        raw_target = Path(save_to).expanduser()
        is_dir_hint = save_to.endswith("/") or raw_target.suffix == ""
        if is_dir_hint:
            base_dir = raw_target
            base_dir.mkdir(parents=True, exist_ok=True)
            return [
                base_dir / f"{_slug(prompt)}-{_timestamp_token()}-{index + 1}.png"
                for index in range(count)
            ]
        if count == 1:
            raw_target.parent.mkdir(parents=True, exist_ok=True)
            return [raw_target]
        raw_target.parent.mkdir(parents=True, exist_ok=True)
        return [
            raw_target.with_name(f"{raw_target.stem}-{index + 1}{raw_target.suffix or '.png'}")
            for index in range(count)
        ]
    base_dir = _default_target_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    token = _timestamp_token()
    slug = _slug(prompt)
    return [
        base_dir / f"{slug}-{token}-{index + 1}.png"
        for index in range(count)
    ]


class ImageGenerationRustGateError(RuntimeError):
    pass


def _enforce_generated_image_file_write(path: Path, payload: bytes) -> None:
    metadata = {
        "path": str(path),
        "payload_bytes": len(bytes(payload or b"")),
        "suffix": path.suffix.lower(),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_generated_image_file",
            state_class="generated_image_files",
            actor_id="system",
            status="active",
            payload=metadata,
            payload_bytes=int(metadata["payload_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_generated_image_file":
            raise ImageGenerationRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise ImageGenerationRustGateError(exc.reason) from exc


def _write_binary(path: Path, payload: bytes) -> str:
    _enforce_generated_image_file_write(path, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return str(path)


def _download_binary(url: str) -> bytes:
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def _openai_client() -> OpenAI:
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if api_key:
        return OpenAI(api_key=api_key)
    return OpenAI()


def _generate_with_openai(
    *,
    prompt: str,
    model: str,
    size: str,
    quality: str,
    n: int,
    save_to: Optional[str],
    client: Optional[Any] = None,
) -> List[str]:
    effective_client = client or _openai_client()
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": n,
        "response_format": "b64_json",
    }
    if model == "dall-e-3":
        payload["quality"] = quality or "standard"
    response = effective_client.images.generate(**payload)
    data = list(getattr(response, "data", None) or (response.get("data") if isinstance(response, dict) else []) or [])
    if not data:
        raise RuntimeError("Image generation returned no images.")
    targets = _target_paths(save_to, count=len(data), prompt=prompt)
    saved: List[str] = []
    for target, item in zip(targets, data):
        b64_json = _item_value(item, "b64_json")
        url = str(_item_value(item, "url") or "").strip()
        if isinstance(b64_json, str) and b64_json.strip():
            binary = base64.b64decode(b64_json)
        elif url:
            binary = _download_binary(url)
        else:
            raise RuntimeError("Image response contained neither b64_json nor url.")
        saved.append(_write_binary(target, binary))
    return saved


def _generate_with_stability(
    *,
    prompt: str,
    size: str,
    n: int,
    save_to: Optional[str],
) -> List[str]:
    targets = _target_paths(save_to, count=n, prompt=prompt)
    stability_key = str(os.getenv("STABILITY_API_KEY") or "").strip()
    if stability_key:
        width, height = size.split("x", 1)
        saved: List[str] = []
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            for target in targets:
                response = client.post(
                    "https://api.stability.ai/v2beta/stable-image/generate/core",
                    headers={
                        "Authorization": f"Bearer {stability_key}",
                        "Accept": "image/*",
                    },
                    data={
                        "prompt": prompt,
                        "output_format": "png",
                        "width": width,
                        "height": height,
                    },
                )
                response.raise_for_status()
                saved.append(_write_binary(target, response.content))
        return saved

    if importlib.util.find_spec("diffusers"):
        from diffusers import StableDiffusionPipeline  # type: ignore

        model_id = str(os.getenv("STABLE_DIFFUSION_MODEL_ID") or "runwayml/stable-diffusion-v1-5").strip()
        pipeline = StableDiffusionPipeline.from_pretrained(model_id)
        images = pipeline(prompt=prompt, num_inference_steps=25, guidance_scale=7.5).images
        if not images:
            raise RuntimeError("Stable Diffusion returned no images.")
        saved: List[str] = []
        for target, image in zip(targets, images[: len(targets)]):
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target)
            saved.append(str(target))
        return saved

    raise RuntimeError("stable-diffusion requires STABILITY_API_KEY or a local diffusers installation.")


def generate_image(
    *,
    prompt: Any,
    model: Any = "dall-e-3",
    size: Any = "1024x1024",
    quality: Any = "standard",
    n: Any = 1,
    save_to: Any = None,
    client: Optional[Any] = None,
) -> List[str]:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise RuntimeError("generate_image requires a prompt.")

    normalized_model = str(model or "dall-e-3").strip().lower() or "dall-e-3"
    if normalized_model not in VALID_MODELS:
        raise RuntimeError(f"Unsupported image model '{normalized_model}'.")

    normalized_size = str(size or "1024x1024").strip().lower() or "1024x1024"
    if normalized_size not in VALID_SIZES:
        raise RuntimeError(f"Unsupported image size '{normalized_size}'.")

    try:
        image_count = int(n or 1)
    except Exception as exc:
        raise RuntimeError("generate_image requires n to be an integer.") from exc
    image_count = max(1, min(image_count, 4))

    normalized_save_to = str(save_to or "").strip() or None
    normalized_quality = str(quality or "standard").strip().lower() or "standard"

    if normalized_model in {"dall-e-3", "dall-e-2"}:
        return _generate_with_openai(
            prompt=normalized_prompt,
            model=normalized_model,
            size=normalized_size,
            quality=normalized_quality,
            n=image_count,
            save_to=normalized_save_to,
            client=client,
        )
    return _generate_with_stability(
        prompt=normalized_prompt,
        size=normalized_size,
        n=image_count,
        save_to=normalized_save_to,
    )
