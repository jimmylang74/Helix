"""
LLM 后端端口 — HelixCore 与 LLM 提供方之间的唯一契约。

LLMBackend 是运行时注入的 Protocol：Host 侧（如 ai_engine 适配器）实现它，
HelixCore 只依赖该抽象，不依赖任何具体 LLM 实现或配置源。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class LLMResponse:
    """Structured LLM response（传输无关）。"""

    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    # ai_engine "usage" event dict (prompt_tokens/completion_tokens/reasoning_tokens); None if unreported.
    usage: Optional[Dict[str, Any]] = None


@runtime_checkable
class LLMBackend(Protocol):
    """LLM 后端接口 — 由 Host 侧实现并注入 AgentOrchestrator。

    方法面与旧 LLMClient 保持一致（ask_json / ask_with_tools / simple_chat /
    refresh），额外提供 get_provider_model 供 token 估算使用。
    """

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        expect_json: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Send a prompt and get a structured response."""
        ...

    def simple_chat(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """One-shot chat — returns plain text."""
        ...

    def ask_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send prompt and expect a JSON document."""
        ...

    def ask_with_tools(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context_messages: Optional[List[Dict[str, str]]] = None,
        emit_stream: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Chat with tool-calling support."""
        ...

    def refresh(self) -> None:
        """Re-read configuration (call after config change)."""
        ...

    def get_provider_model(self) -> tuple[str, str]:
        """Return ``(provider, model)`` for token estimation.

        E.g. ``("ollama_native", "qwen2.5:7b")``.
        """
        ...
