"""
Token counting / estimation utilities for the agent layer.

Class hierarchy:

    TokenEstimator (base)
      ├── SimpleEstimator      — UTF-8 byte-based rough count (default, no deps)
      ├── TiktokenEstimator    — OpenAI tiktoken BPE (fast, stable)
      ├── HFEstimator          — HuggingFace transformers AutoTokenizer (model-based)
      └── OllamaUsageCounter   — actual counts read from an Ollama/litellm usage payload

tiktoken / transformers are optional dependencies: they are imported lazily
inside the respective estimators so this module always imports cleanly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Callable, ClassVar, Protocol, cast, final, override

__all__ = [
    "TokenUsage",
    "TokenEstimator",
    "SimpleEstimator",
    "TiktokenEstimator",
    "HFEstimator",
    "OllamaUsageCounter",
    "create_estimator",
    "create_estimator_for_config",
]


# ═══════════════════════════════════════════════════════════════════════
# Token usage record
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TokenUsage:
    """Actual or estimated token usage of a request."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_tokens


# ═══════════════════════════════════════════════════════════════════════
# Base class
# ═══════════════════════════════════════════════════════════════════════


class TokenEstimator(ABC):
    """Base class for token estimators / counters.

    Subclasses must implement :meth:`count_tokens`; :meth:`count_messages`
    and :meth:`estimate_usage` are built on top of it.
    """

    name: ClassVar[str] = "base"

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count / estimate the number of tokens in ``text``."""

    def count_messages(self, messages: Iterable[Mapping[str, object]]) -> int:
        """Count / estimate total tokens across a message list.

        ``messages`` is a list of ``{"role": ..., "content": ...}`` dicts
        (the format used by ``LLMClient``); entries without content are
        skipped.
        """
        total = 0
        for msg in messages:
            content = msg.get("content")
            if content:
                total += self.count_tokens(str(content))
        return total

    def estimate_usage(self, input_text: str, output_text: str = "") -> TokenUsage:
        """Estimate input/output token usage for a request."""
        return TokenUsage(
            input_tokens=self.count_tokens(input_text),
            output_tokens=self.count_tokens(output_text) if output_text else 0,
        )


# ═══════════════════════════════════════════════════════════════════════
# SimpleEstimator — UTF-8 byte-based (default)
# ═══════════════════════════════════════════════════════════════════════


@final
class SimpleEstimator(TokenEstimator):
    """Rough byte-based counter (default). No external dependencies.

    Returns the UTF-8 byte length of the text. This over-estimates token
    counts for CJK text (~3 bytes per token vs ~1 token per char) and
    under-estimates for English (~1 token per 4 bytes); use it only for
    rough comparisons or as a zero-dependency fallback.
    """

    name = "simple"

    @override
    def count_tokens(self, text: str) -> int:
        return len(text.encode("utf-8"))


# ═══════════════════════════════════════════════════════════════════════
# TiktokenEstimator — OpenAI tiktoken BPE
# ═══════════════════════════════════════════════════════════════════════


@final
class TiktokenEstimator(TokenEstimator):
    """Estimates tokens with OpenAI's tiktoken BPE tokenizer.

    Fast and stable; a good general-purpose estimate for most chat models.
    Requires ``pip install tiktoken`` (imported lazily).

    Args:
        encoding_name: tiktoken encoding name, e.g. ``"cl100k_base"``
            (default) or ``"o200k_base"``.
        model_name: optional model name (e.g. ``"gpt-4o"``); resolved via
            ``tiktoken.encoding_for_model`` and takes precedence over
            ``encoding_name``.
    """

    name = "tiktoken"

    def __init__(
        self,
        encoding_name: str = "cl100k_base",
        model_name: str | None = None,
    ):
        try:
            import tiktoken
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "TiktokenEstimator requires 'pip install tiktoken'"
            ) from exc

        if model_name:
            self._encoding = tiktoken.encoding_for_model(model_name)
        else:
            self._encoding = tiktoken.get_encoding(encoding_name)

    @override
    def count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))


# ═══════════════════════════════════════════════════════════════════════
# HFEstimator — HuggingFace transformers AutoTokenizer
# ═══════════════════════════════════════════════════════════════════════


class _Tokenizer(Protocol):
    """Minimal surface of a tokenizer that ``HFEstimator`` relies on."""

    def encode(self, text: str, add_special_tokens: bool = ...) -> list[int]: ...


@final
class HFEstimator(TokenEstimator):
    """Counts tokens with a HuggingFace transformers tokenizer.

    The tokenizer matches the exact model family, so this is the most
    accurate option for locally served models (e.g. Ollama's qwen).
    Requires ``pip install transformers`` (imported lazily). The tokenizer
    is loaded once at construction time; the first load may download files
    from the HuggingFace Hub.

    Args:
        model_name: HuggingFace model id or local path,
            e.g. ``"Qwen/Qwen2.5-7B-Instruct"``.
    """

    name = "hf"

    def __init__(self, model_name: str):
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "HFEstimator requires 'pip install transformers'"
            ) from exc

        self._model_name = model_name
        # transformers stubs leave ``from_pretrained``'s parameters
        # unannotated (Unknown); resolve it via getattr + cast so the
        # Unknown does not leak into our public API.
        factory = cast(
            "Callable[[str], _Tokenizer]",
            getattr(AutoTokenizer, "from_pretrained"),
        )
        self._tokenizer = factory(model_name)

    @override
    def count_tokens(self, text: str) -> int:
        # add_special_tokens=False: count only content tokens (approximation)
        return len(self._tokenizer.encode(text, add_special_tokens=False))


# ═══════════════════════════════════════════════════════════════════════
# OllamaUsageCounter — real counts from an Ollama / litellm usage payload
# ═══════════════════════════════════════════════════════════════════════


def _to_int(value: object) -> int:
    """Coerce a reported token count to int; non-numeric values become 0."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return 0


@final
class OllamaUsageCounter(TokenEstimator):
    """Counts actual tokens from an Ollama / litellm usage payload.

    Unlike the estimators above, this does NOT estimate from raw text — it
    reads the real counts reported by the model response.

    Research note (ai_engine integration):
        ai_engine runs Ollama through litellm, which normalizes Ollama's
        native ``prompt_eval_count`` / ``eval_count`` into ``prompt_tokens``
        / ``completion_tokens`` in the emitted ``usage`` event (see
        ``litellm.llms.ollama.chat.transformation``). The raw Ollama keys
        are NOT passed through by ai_engine today, so this counter:

        1. prefers the raw keys (``prompt_eval_count`` / ``eval_count``)
           when the payload contains them (native ``ollama`` client
           responses, or a future ai_engine that forwards them);
        2. falls back to the litellm-normalized keys (``prompt_tokens`` /
           ``completion_tokens``) emitted by ai_engine — for Ollama these
           are exactly the eval counts.

    Args:
        fallback: estimator used by the inherited text-based methods
            (:meth:`count_tokens` / :meth:`count_messages`) for pre-call
            estimates. Defaults to :class:`SimpleEstimator`.
    """

    name = "ollama_usage"

    def __init__(self, fallback: TokenEstimator | None = None):
        self._fallback = fallback if fallback is not None else SimpleEstimator()

    @override
    def count_tokens(self, text: str) -> int:
        """Pre-call estimate for raw text; real counts come from :meth:`count_usage`."""
        return self._fallback.count_tokens(text)

    def count_usage(self, usage: object | None = None) -> TokenUsage:
        """Extract input/output token counts from a usage payload.

        Accepts a dict (e.g. the ``usage`` event emitted by ai_engine, or a
        native ``ollama`` client response dict) or any object exposing the
        matching attributes (e.g. litellm ``ModelResponse.usage``). Missing
        fields fall back to 0.
        """
        if usage is None:
            return TokenUsage()

        if isinstance(usage, Mapping):
            data = cast(Mapping[str, object], usage)
            input_tokens = (
                data.get("prompt_eval_count") or data.get("prompt_tokens") or 0
            )
            output_tokens = data.get("eval_count") or data.get("completion_tokens") or 0
            reasoning_tokens = data.get("reasoning_tokens") or 0
        else:
            input_tokens = (
                getattr(usage, "prompt_eval_count", None)
                or getattr(usage, "prompt_tokens", None)
                or 0
            )
            output_tokens = (
                getattr(usage, "eval_count", None)
                or getattr(usage, "completion_tokens", None)
                or 0
            )
            reasoning_tokens = getattr(usage, "reasoning_tokens", None) or 0

        return TokenUsage(
            input_tokens=_to_int(input_tokens),
            output_tokens=_to_int(output_tokens),
            reasoning_tokens=_to_int(reasoning_tokens),
        )


# ═══════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════

_ESTIMATORS: dict[str, type[TokenEstimator]] = {
    "simple": SimpleEstimator,
    "tiktoken": TiktokenEstimator,
    "hf": HFEstimator,
    "ollama": OllamaUsageCounter,
}


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def create_estimator(kind: str = "simple", **kwargs: object) -> TokenEstimator:
    """Factory: return a :class:`TokenEstimator` by kind.

    :class:`SimpleEstimator` is the default when ``kind`` is empty/omitted.
    Supported kinds: ``"simple"``, ``"tiktoken"``, ``"hf"``, ``"ollama"``.
    """
    cls = _ESTIMATORS.get((kind or "simple").lower())
    if cls is None:
        raise ValueError(
            f"Unknown estimator kind: {kind!r}. Supported: {sorted(_ESTIMATORS)}"
        )

    if cls is SimpleEstimator:
        return SimpleEstimator()
    if cls is TiktokenEstimator:
        return TiktokenEstimator(
            encoding_name=str(kwargs.get("encoding_name", "cl100k_base")),
            model_name=_optional_str(kwargs.get("model_name")),
        )
    if cls is HFEstimator:
        model_name = kwargs.get("model_name")
        if not isinstance(model_name, str):
            raise TypeError(
                "HFEstimator requires a 'model_name' string"
                + " (e.g. 'Qwen/Qwen2.5-7B-Instruct')"
            )
        return HFEstimator(model_name=model_name)

    fallback = kwargs.get("fallback")
    if fallback is not None and not isinstance(fallback, TokenEstimator):
        raise TypeError("'fallback' must be a TokenEstimator instance")
    return OllamaUsageCounter(fallback=fallback)


def _is_local_ollama(provider: str) -> bool:
    """Whether ``provider`` refers to the local Ollama server."""
    return provider.strip().lower() in ("ollama", "ollama_native")


def _estimate_chain(model: str, simple_last_resort: bool = False) -> TokenEstimator:
    """Pick the most accurate estimator that can be constructed for ``model``.

    Rungs: HF tokenizer (exact model family) → tiktoken (model-specific
    encoding, then generic ``cl100k_base``). Each rung is skipped on
    construction failure (missing dependency or unresolvable model id).
    With ``simple_last_resort`` set, a byte-based simple counter catches
    any remaining failure; otherwise tiktoken is the guaranteed floor and
    a missing install raises ImportError.
    """
    try:
        return HFEstimator(model_name=model)
    except (ImportError, OSError, ValueError):
        pass
    try:
        return TiktokenEstimator(model_name=model)
    except (ImportError, KeyError):
        pass
    try:
        return TiktokenEstimator()
    except ImportError:
        pass
    if simple_last_resort:
        return SimpleEstimator()
    raise ImportError("tiktoken is required for non-Ollama token estimation; run 'pip install tiktoken'")


def create_estimator_for_config(
    *,
    provider: str,
    model: str,
    streaming: bool,
) -> TokenEstimator:
    """Return the best :class:`TokenEstimator` for an LLM configuration.

    Local Ollama: non-streaming calls return :class:`OllamaUsageCounter`
    so real response counts are read from the ``usage`` payload; streaming
    calls (litellm drops usage on the stream path) fall back through
    HF → tiktoken → byte-based simple. Non-Ollama providers go straight to
    HF → tiktoken, with generic tiktoken as the guaranteed floor. Inspect
    the returned estimator's ``name`` to see which rung was chosen.
    """
    if _is_local_ollama(provider):
        if streaming:
            return _estimate_chain(model, simple_last_resort=True)
        return OllamaUsageCounter()
    return _estimate_chain(model)
