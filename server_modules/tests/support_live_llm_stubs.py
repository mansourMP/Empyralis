"""Stubs for the provider calls a Sage / direct-chat turn actually makes.

Why this module exists
----------------------
Every test that drove ``sage_agent_runtime_service.handle_sage_chat`` used to
patch ``sage_agent_runtime_service.generate_chat_reply_with_provider_fallback``
and believe it was covered. It was not: that function is no longer on the
turn's live path. The turn runs

    handle_sage_chat
      -> _run_sage_action_loop_v3
        -> direct_chat_generation_service.stream_provider_backed_direct_chat
          -> services.generate_chat_reply_stream_with_provider_fallback   (1)
        ...and after the reply is yielded...
        -> services.persist_direct_chat_memory_best_effort
          -> memory_service.persist_direct_chat_memory_best_effort
            -> generate_reply(...)                                       (2)

...plus a third whenever ``tool_honesty_guard`` decides the reply needs a
correction pass. So one Sage turn makes up to THREE provider calls, and the
old patch guarded none of them. The non-streaming name survives only on
fallback paths
(``sage_agent_runtime_service`` lines 4104 / 5885 / 6573), which is why the
mock looked like it worked: it was a real mock on a real function that the
ordinary turn simply never reaches. Exactly the failure mode CLAUDE.md names
-- "a mock protects a seam, not a path".

Both call sites are stubbed here, at the narrowest module attribute that is
actually reachable from a test:

  (1) ``direct_chat_runtime_exports.generate_chat_reply_stream_with_provider_fallback``
      -- the direct-chat binding resolves this one lazily out of that
      module's namespace on every turn, so patching the attribute really
      does redirect the call. Everything downstream of it
      (``stream_provider_backed_direct_chat``'s delta de-duplication, final
      payload assembly, usage accounting, tool loop) stays REAL and under
      test, which is the point: a stub that returned one canned reply string
      would make a streaming test green while proving nothing about how the
      stream is assembled.

  (2) ``memory_service.persist_direct_chat_memory_best_effort`` -- the
      post-reply memory-fact extraction. Its ``generate_reply`` is injected
      as a *parameter* (bound by value at ``direct_chat_runtime_exports``
      import time via ``generate_reply_fn=``), so there is no module
      attribute further in to patch; this function IS the narrowest seam.
      It is never the subject of the tests that use this helper.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Sequence
from unittest.mock import MagicMock, patch

# The one function every in-process direct-chat/Sage turn uses to reach a
# provider. See the module docstring for why this exact dotted path (and not
# scripts.orion_local_worker_llm's own definition, which is bound by value at
# import time and therefore unpatchable from here).
SAGE_STREAM_TARGET = (
    "server_modules.direct_chat_runtime_exports."
    "generate_chat_reply_stream_with_provider_fallback"
)

# The second, post-reply provider call: memory-fact extraction.
MEMORY_EXTRACTION_TARGET = "server_modules.memory_service.persist_direct_chat_memory_best_effort"

# The third: tool_honesty_guard's regeneration pass, which fires whenever a
# reply looks like it denied or faked a tool call. Reached through a
# FUNCTION-LOCAL import inside direct_chat_generation_service's
# _direct_chat_regenerate, so unlike generate_reply_fn (bound by value at
# import time and therefore unpatchable) this module attribute is read fresh
# on every call and a patch here really does intercept it.
HONESTY_GUARD_REGENERATION_TARGET = (
    "server_modules.direct_chat_runtime_exports.generate_chat_reply_with_provider_fallback"
)


def _default_chunks(reply: str) -> List[str]:
    """Split a reply into >1 stream deltas so the assembly logic under test
    has something real to assemble. A single-chunk stream would not exercise
    the incremental-prefix path in stream_provider_backed_direct_chat."""
    text = str(reply or "")
    if len(text) < 2:
        return [text] if text else []
    midpoint = len(text) // 2
    return [text[:midpoint], text[midpoint:]]


class ChatStreamStub:
    """Stands in for ``generate_chat_reply_stream_with_provider_fallback``.

    Emits a genuine multi-delta stream followed by the terminal event the
    real provider adapter emits, and records every call so a test can assert
    on the system prompt / context / history the turn actually sent -- the
    same values the old, dead ``mock_generate.call_args`` was reaching for.
    """

    def __init__(
        self,
        *,
        reply: str = "Reply",
        usage: Optional[Dict[str, Any]] = None,
        provider: str = "deepseek",
        model: str = "",
        error: str = "",
        attempted_providers: str = "",
        tool_calls: Optional[Sequence[Dict[str, Any]]] = None,
        chunks: Optional[Sequence[str]] = None,
        raises: Optional[BaseException] = None,
    ) -> None:
        # `raises` models a transport that blew up rather than answering --
        # the streaming equivalent of `side_effect=RuntimeError(...)` on the
        # old (dead) non-streaming seam.
        self.raises = raises
        self.reply = str(reply or "")
        self.usage = dict(usage or {})
        self.provider = str(provider or "")
        self.model = str(model or self.usage.get("model") or "")
        self.error = str(error or "")
        self.attempted_providers = str(attempted_providers or self.provider or "")
        self.tool_calls = [dict(call) for call in (tool_calls or [])]
        self.chunks = list(chunks) if chunks is not None else _default_chunks(self.reply)
        self.calls: List[Dict[str, Any]] = []

    # -- the callable the production code invokes --------------------------
    def __call__(self, **kwargs: Any) -> Iterator[Dict[str, Any]]:
        self.calls.append(dict(kwargs))
        if self.raises is not None:
            raise self.raises
        return iter(self._events())

    def _events(self) -> List[Dict[str, Any]]:
        # A provider that produced no text but did report an error emits a
        # "failure" event, never an empty "result" -- mirroring the real
        # adapter, so an error-path test exercises the error path.
        if self.error and not self.reply:
            return [{
                "type": "failure",
                "attempted_providers": self.attempted_providers,
                "error": self.error,
            }]
        events: List[Dict[str, Any]] = [
            {"type": "chunk", "delta": delta} for delta in self.chunks if delta
        ]
        events.append({
            "type": "result",
            "reply": self.reply,
            "usage_masked": dict(self.usage),
            "provider": self.provider or None,
            "model": self.model or None,
            "attempted_providers": self.attempted_providers,
            "error": self.error,
            "tool_calls": list(self.tool_calls),
        })
        return events

    # -- what the turn actually sent --------------------------------------
    @property
    def call_count(self) -> int:
        return len(self.calls)

    def _last(self) -> Dict[str, Any]:
        assert self.calls, (
            "the provider stream stub was never called -- the turn did not reach "
            f"{SAGE_STREAM_TARGET}, so this test is asserting on a call that never "
            "happened. Find where the path goes now before re-pointing the stub."
        )
        return self.calls[-1]

    @property
    def system_prompt(self) -> str:
        return str(self._last().get("system_prompt") or "")

    @property
    def context(self) -> Dict[str, Any]:
        value = self._last().get("context")
        return value if isinstance(value, dict) else {}

    @property
    def metadata(self) -> Dict[str, Any]:
        value = self._last().get("metadata")
        return value if isinstance(value, dict) else {}

    @property
    def user_goal(self) -> str:
        return str(self._last().get("user_goal") or "")

    @property
    def prior_messages(self) -> List[Dict[str, Any]]:
        value = self._last().get("prior_messages")
        return list(value) if isinstance(value, list) else []


class patched_provider_calls:  # noqa: N801 -- reads as a context manager at call sites
    """Context manager stubbing every provider call one turn makes; yields
    the stream stub so a test can assert on what the turn actually sent.

    ``stream_target`` exists because test_operator_chat.py loads a SECOND,
    independent copy of direct_chat_runtime_exports under the module name
    "operator_chat_under_test"; a patch on one module object does nothing to
    the other.
    """

    def __init__(
        self,
        stream: Optional[ChatStreamStub] = None,
        *,
        stream_target: str = SAGE_STREAM_TARGET,
        patch_memory_extraction: bool = True,
        patch_honesty_guard_regeneration: bool = True,
        regeneration_reply: str = "",
        **stream_kwargs: Any,
    ) -> None:
        if stream is not None and stream_kwargs:
            raise TypeError("pass either a prebuilt ChatStreamStub or its keyword arguments, not both")
        self.stream = stream if stream is not None else ChatStreamStub(**stream_kwargs)
        self._patches = [patch(stream_target, new=self.stream)]
        if patch_memory_extraction:
            self._patches.append(patch(MEMORY_EXTRACTION_TARGET, new=MagicMock()))
        if patch_honesty_guard_regeneration:
            # Default is "regeneration declined to produce anything", which
            # leaves tool_honesty_guard on its own no-correction path rather
            # than inventing a second model reply the test never asked for.
            self.regeneration = MagicMock(
                return_value=(regeneration_reply, {}, self.stream.provider, "")
            )
            self._patches.append(patch(HONESTY_GUARD_REGENERATION_TARGET, new=self.regeneration))
        else:
            self.regeneration = None

    def __enter__(self) -> ChatStreamStub:
        for item in self._patches:
            item.start()
        return self.stream

    def __exit__(self, *exc_info: Any) -> None:
        for item in reversed(self._patches):
            item.stop()
