from __future__ import annotations

import asyncio
import base64
import io
import os
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

import httpx


Modality = Literal[
    "speech_to_text",
    "text_to_speech",
    "image_generation",
    "image_editing",
    "video_generation",
    "realtime_voice",
    "vision_understanding",
]

SUPPORTED_STT_CONTENT_TYPES: Dict[str, str] = {
    "audio/webm": "input.webm",
    "audio/wav": "input.wav",
    "audio/wave": "input.wav",
    "audio/x-wav": "input.wav",
}
TTS_MAX_CHARS = 4096
TTS_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}


class MultimodalProviderError(RuntimeError):
    pass


class MultimodalProviderUnavailable(MultimodalProviderError):
    pass


class UnsupportedModalityError(MultimodalProviderError):
    pass


@dataclass(frozen=True, slots=True)
class MultimodalProviderMetadata:
    provider: str
    model: str
    modality: Modality
    latency_class: str
    cost_class: str
    retention_policy: str
    supports_streaming: bool = False
    supports_batch: bool = False
    max_input_size: Optional[int] = None
    output_format: str = ""
    safety_notes: tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "modality": self.modality,
            "latency_class": self.latency_class,
            "cost_class": self.cost_class,
            "retention_policy": self.retention_policy,
            "supports_streaming": self.supports_streaming,
            "supports_batch": self.supports_batch,
            "max_input_size": self.max_input_size,
            "output_format": self.output_format,
            "safety_notes": list(self.safety_notes),
        }


def _env_text(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def _normalized_openai_api_key() -> str:
    return _env_text("OPENAI_API_KEY")


def _normalized_elevenlabs_api_key() -> str:
    return _env_text("ELEVENLABS_API_KEY") or _env_text("ELEVENLABS_APIKEY")


def _provider_env_for_modality(modality: Modality) -> str:
    env_name = {
        "speech_to_text": "EMPYRALIS_STT_PROVIDER",
        "text_to_speech": "EMPYRALIS_TTS_PROVIDER",
        "image_generation": "EMPYRALIS_IMAGE_PROVIDER",
        "image_editing": "EMPYRALIS_IMAGE_PROVIDER",
        "video_generation": "EMPYRALIS_VIDEO_PROVIDER",
        "realtime_voice": "EMPYRALIS_REALTIME_VOICE_PROVIDER",
        "vision_understanding": "EMPYRALIS_VISION_PROVIDER",
    }.get(modality)
    return _env_text(env_name or "", "auto").lower() or "auto"


def provider_metadata(*, modality: Modality, provider: str = "auto", model: str = "") -> MultimodalProviderMetadata:
    resolved_provider = str(provider or "auto").strip().lower() or "auto"
    resolved_model = str(model or "").strip()
    if modality == "speech_to_text":
        if resolved_provider == "elevenlabs":
            resolved_model = resolved_model or _env_text("ELEVENLABS_STT_MODEL", "scribe_v1")
            return MultimodalProviderMetadata(
                provider="elevenlabs",
                model=resolved_model,
                modality=modality,
                latency_class="interactive",
                cost_class="metered",
                retention_policy="provider_configured",
                output_format="text/json",
                safety_notes=("Transcript must enter Sage through voice policy before actions execute.",),
            )
        if resolved_provider in {"google", "google_speech"}:
            return MultimodalProviderMetadata(
                provider="google_speech",
                model="recognize_google",
                modality=modality,
                latency_class="interactive",
                cost_class="metered",
                retention_policy="provider_configured",
                output_format="text/json",
                safety_notes=("Google speech fallback supports WAV audio only.",),
            )
        resolved_model = resolved_model or _env_text("OPENAI_STT_MODEL", "whisper-1")
        return MultimodalProviderMetadata(
            provider="openai",
            model=resolved_model,
            modality=modality,
            latency_class="interactive",
            cost_class="metered",
            retention_policy="provider_configured",
            output_format="text/json",
            safety_notes=("Transcript must enter Sage through voice policy before actions execute.",),
        )
    if modality == "text_to_speech":
        if resolved_provider == "elevenlabs":
            resolved_model = resolved_model or _env_text("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2")
            return MultimodalProviderMetadata(
                provider="elevenlabs",
                model=resolved_model,
                modality=modality,
                latency_class="interactive",
                cost_class="metered",
                retention_policy="provider_configured",
                output_format="audio/mpeg",
                safety_notes=("TTS is output only and cannot resolve approvals.",),
            )
        resolved_model = resolved_model or _env_text("OPENAI_TTS_MODEL", "tts-1")
        return MultimodalProviderMetadata(
            provider="openai",
            model=resolved_model,
            modality=modality,
            latency_class="interactive",
            cost_class="metered",
            retention_policy="provider_configured",
            output_format="audio/mpeg",
            safety_notes=("TTS is output only and cannot resolve approvals.",),
        )
    raise UnsupportedModalityError(f"{modality} provider interface exists, but no provider is configured.")


def _resolve_stt_provider(content_type: str, provider: str = "auto") -> str:
    requested = str(provider or "").strip().lower() or _provider_env_for_modality("speech_to_text")
    if requested not in {"", "auto"}:
        return requested
    if _normalized_openai_api_key():
        return "openai"
    if _normalized_elevenlabs_api_key():
        return "elevenlabs"
    if content_type in {"audio/wav", "audio/wave", "audio/x-wav"}:
        return "google_speech"
    return "openai"


def _resolve_tts_provider(provider: str = "auto") -> str:
    requested = str(provider or "").strip().lower() or _provider_env_for_modality("text_to_speech")
    if requested not in {"", "auto"}:
        return requested
    if _normalized_openai_api_key():
        return "openai"
    if _normalized_elevenlabs_api_key():
        return "elevenlabs"
    return "openai"


async def _transcribe_with_openai(audio_bytes: bytes, content_type: str) -> Dict[str, Any]:
    api_key = _normalized_openai_api_key()
    if not api_key:
        raise MultimodalProviderUnavailable("OPENAI_API_KEY is not configured.")
    filename = SUPPORTED_STT_CONTENT_TYPES.get(content_type, "input.webm")
    metadata = provider_metadata(modality="speech_to_text", provider="openai")
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": metadata.model, "response_format": "json"},
            files={"file": (filename, audio_bytes, content_type)},
        )
    response.raise_for_status()
    payload = response.json()
    transcript = str(payload.get("text") or "").strip()
    return {
        "transcript": transcript,
        "confidence": 1.0 if transcript else 0.0,
        "provider": metadata.provider,
        "model": metadata.model,
        "metadata": metadata.as_dict(),
    }


async def _transcribe_with_elevenlabs(audio_bytes: bytes, content_type: str) -> Dict[str, Any]:
    api_key = _normalized_elevenlabs_api_key()
    if not api_key:
        raise MultimodalProviderUnavailable("ELEVENLABS_API_KEY is not configured.")
    metadata = provider_metadata(modality="speech_to_text", provider="elevenlabs")
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        response = await client.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": api_key},
            data={"model_id": metadata.model},
            files={"file": ("input", audio_bytes, content_type)},
        )
    response.raise_for_status()
    payload = response.json()
    transcript = str(payload.get("text") or payload.get("transcript") or "").strip()
    return {
        "transcript": transcript,
        "confidence": float(payload.get("confidence") or (1.0 if transcript else 0.0)),
        "provider": metadata.provider,
        "model": metadata.model,
        "metadata": metadata.as_dict(),
    }


def _transcribe_with_google(audio_bytes: bytes, content_type: str) -> Dict[str, Any]:
    if content_type not in {"audio/wav", "audio/wave", "audio/x-wav"}:
        raise MultimodalProviderUnavailable("Google speech fallback only supports WAV audio.")
    try:
        import speech_recognition as sr  # type: ignore
    except ImportError as exc:
        raise MultimodalProviderUnavailable("Google speech fallback is unavailable.") from exc
    recognizer = sr.Recognizer()
    with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
        audio = recognizer.record(source)
    transcript = str(recognizer.recognize_google(audio) or "").strip()
    metadata = provider_metadata(modality="speech_to_text", provider="google_speech")
    return {
        "transcript": transcript,
        "confidence": 0.6 if transcript else 0.0,
        "provider": metadata.provider,
        "model": metadata.model,
        "metadata": metadata.as_dict(),
    }


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    content_type: str,
    *,
    provider: str = "auto",
) -> Dict[str, Any]:
    if not audio_bytes:
        raise ValueError("Audio payload is required.")
    normalized_content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type not in SUPPORTED_STT_CONTENT_TYPES:
        raise ValueError("Unsupported audio format.")
    resolved_provider = _resolve_stt_provider(normalized_content_type, provider)
    if resolved_provider == "openai":
        return await _transcribe_with_openai(audio_bytes, normalized_content_type)
    if resolved_provider == "elevenlabs":
        return await _transcribe_with_elevenlabs(audio_bytes, normalized_content_type)
    if resolved_provider in {"google", "google_speech"}:
        return await asyncio.to_thread(_transcribe_with_google, audio_bytes, normalized_content_type)
    raise MultimodalProviderUnavailable(f"Speech-to-text provider is not configured: {resolved_provider}.")


def split_tts_text(text: str, max_chars: int = TTS_MAX_CHARS) -> List[str]:
    normalized = re.sub(r"[ \t]+\n", "\n", str(text or "")).strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    def flush_chunk(chunks: List[str], current: str) -> str:
        value = current.strip()
        if value:
            chunks.append(value)
        return ""

    chunks: List[str] = []
    current = ""
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
    for paragraph in paragraphs:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", paragraph) if part.strip()]
        if not sentences:
            sentences = [paragraph]
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
                continue
            current = flush_chunk(chunks, current)
            if len(sentence) <= max_chars:
                current = sentence
                continue
            start = 0
            while start < len(sentence):
                slice_end = min(start + max_chars, len(sentence))
                piece = sentence[start:slice_end].strip()
                if piece:
                    chunks.append(piece)
                start = slice_end
        current = flush_chunk(chunks, current)
    flush_chunk(chunks, current)
    return chunks or [normalized[:max_chars]]


async def _synthesize_with_openai(text: str, voice: str, speed: float) -> bytes:
    api_key = _normalized_openai_api_key()
    if not api_key:
        raise MultimodalProviderUnavailable("OPENAI_API_KEY is not configured.")
    metadata = provider_metadata(modality="text_to_speech", provider="openai")
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": metadata.model,
                "voice": voice,
                "input": text,
                "speed": speed,
                "format": "mp3",
            },
        )
    response.raise_for_status()
    return bytes(response.content)


async def _synthesize_with_elevenlabs(text: str, voice: str, speed: float) -> bytes:
    api_key = _normalized_elevenlabs_api_key()
    if not api_key:
        raise MultimodalProviderUnavailable("ELEVENLABS_API_KEY is not configured.")
    metadata = provider_metadata(modality="text_to_speech", provider="elevenlabs")
    voice_id = _env_text("ELEVENLABS_VOICE_ID") or voice
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        response = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "model_id": metadata.model,
                "text": text,
                "voice_settings": {"speed": speed},
            },
        )
    response.raise_for_status()
    return bytes(response.content)


async def synthesize_speech_chunks(
    text: str,
    voice: str,
    speed: float,
    *,
    provider: str = "auto",
) -> List[bytes]:
    parts = split_tts_text(text)
    if not parts:
        raise ValueError("Text is required.")
    resolved_provider = _resolve_tts_provider(provider)
    results: List[bytes] = []
    for part in parts:
        if resolved_provider == "openai":
            results.append(await _synthesize_with_openai(part, voice, speed))
        elif resolved_provider == "elevenlabs":
            results.append(await _synthesize_with_elevenlabs(part, voice, speed))
        else:
            raise MultimodalProviderUnavailable(f"Text-to-speech provider is not configured: {resolved_provider}.")
    return results


def assert_modality_configured(modality: Modality, *, provider: str = "auto") -> MultimodalProviderMetadata:
    requested = str(provider or "").strip().lower() or _provider_env_for_modality(modality)
    if modality in {"speech_to_text", "text_to_speech"}:
        return provider_metadata(modality=modality, provider=requested)
    raise UnsupportedModalityError(f"{modality} is interface-only and is not configured for production output.")




async def describe_image(
    *,
    file_path: str,
    filename: str,
    content_type: str = "",
    api_key: str | None = None,
) -> str:
    """Describe an image using Gemini API for text-only models like DeepSeek."""
    import base64
    import mimetypes
    from pathlib import Path
    
    resolved_key = str(api_key or os.environ.get("EMPYRALIS_VISION_API_KEY") or "").strip()
    if not resolved_key:
        return f"(Image: {filename} — vision description unavailable.)"
    
    path = Path(file_path) if isinstance(file_path, str) else file_path
    if not path.exists():
        return f"(Image: {filename} — file not found.)"
    
    mime = content_type or mimetypes.guess_type(filename)[0] or "image/jpeg"
    try:
        raw = path.read_bytes()
        encoded = base64.b64encode(raw).decode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={resolved_key}"
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Describe this image concisely for a text-only AI assistant. Focus on key visual elements, text content, people, objects, and context. If there is readable text, include it. Keep under 100 words."},
                    {"inline_data": {"mime_type": mime, "data": encoded}}
                ]
            }]
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                return f"(Image: {filename} — could not describe.)"
            result = resp.json()
            candidates = result.get("candidates", [])
            if not candidates:
                return f"(Image: {filename} — no description.)"
            parts = candidates[0].get("content", {}).get("parts", [])
            text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
            if text:
                return f"(Image: {filename} — {text})"
            return f"(Image: {filename} — no description.)"
    except Exception:
        return f"(Image: {filename} — description unavailable.)"


async def generate_image(*_args: Any, provider: str = "auto", **_kwargs: Any) -> Dict[str, Any]:
    raise UnsupportedModalityError(
        f"image_generation is interface-only and not configured for provider {provider or 'auto'}."
    )


async def edit_image(*_args: Any, provider: str = "auto", **_kwargs: Any) -> Dict[str, Any]:
    raise UnsupportedModalityError(
        f"image_editing is interface-only and not configured for provider {provider or 'auto'}."
    )


async def generate_video(*_args: Any, provider: str = "auto", **_kwargs: Any) -> Dict[str, Any]:
    raise UnsupportedModalityError(
        f"video_generation is interface-only and not configured for provider {provider or 'auto'}."
    )
