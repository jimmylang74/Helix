"""
HelixCore.interface — 注入端口抽象基类统一定义（唯一契约文件）。

HelixCore 与 Host 的边界由注入端口定义，全部集中在本文件：
- LLMBackend     — LLM 调用抽象（Host 侧实现，如 modules.host.ai_engine_backend.AIEngineBackend）
- EventSink      — 前端事件输出抽象（Host 侧实现，如 modules.host.event_sink.SSEEventSink）
- LlmEventBus    — LLM 交互事件流 + 请求上下文抽象（Host 侧实现，如 modules.host.llm_event_bus.LlmEventBusImpl）
- IntentProvider — 意图配置抽象（Host 侧实现，如 modules.host.intent_store.IntentStore）
- LogSink        — 日志输出抽象（Host 侧实现，如 modules.host.log_sink.HostLogSink）

各端口均为运行时注入的抽象基类（ABC）：Host 侧实现类继承它，由组合根
实例化后显式注入（AgentOrchestrator / ToolRegistry.set_logger /
set_intent_provider）。HelixCore 只依赖本文件定义的抽象，不依赖任何具体
实现或配置源；NullLogSink 为内置静默实现，保证未注入时零外部依赖可独立运行。

LLMResponse 是 LLMBackend 的传输无关返回结构（dataclass 值对象）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── LLM 后端 ──────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Structured LLM response（传输无关）。"""

    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    # ai_engine "usage" event dict (prompt_tokens/completion_tokens/reasoning_tokens); None if unreported.
    usage: Optional[Dict[str, Any]] = None


class LLMBackend(ABC):
    """LLM 后端抽象基类 — 由 Host 侧实现并注入 AgentOrchestrator。

    方法面与旧 LLMClient 保持一致（ask_json / ask_with_tools / simple_chat /
    refresh），额外提供 get_provider_model 供 token 估算使用。
    """

    @abstractmethod
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

    @abstractmethod
    def simple_chat(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """One-shot chat — returns plain text."""
        ...

    @abstractmethod
    def ask_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send prompt and expect a JSON document."""
        ...

    @abstractmethod
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

    @abstractmethod
    def refresh(self) -> None:
        """Re-read configuration (call after config change)."""
        ...

    @abstractmethod
    def get_provider_model(self) -> tuple[str, str]:
        """Return ``(provider, model)`` for token estimation.

        E.g. ``("ollama_native", "qwen2.5:7b")``.
        """
        ...


# ── 事件输出 ──────────────────────────────────────────────────────────────

class EventSink(ABC):
    """事件输出抽象基类 — 由 Host 侧实现并注入 AgentOrchestrator。

    ``emit`` 推送一次状态快照（可附带 DAG 节点图与单节点结果），
    ``cleanup`` 释放某个请求的全部事件缓冲与消费者队列。
    """

    @abstractmethod
    def emit(
        self,
        request_id: str,
        state: Dict[str, Any],
        graph_nodes: Optional[List[Dict[str, Any]]] = None,
        node_result: Optional[Dict[str, Any]] = None,
        completed: bool = False,
    ) -> None:
        """Emit a status snapshot for ``request_id`` (transport-agnostic)."""
        ...

    @abstractmethod
    def cleanup(self, request_id: str) -> None:
        """Release all buffered events and consumer queues for ``request_id``."""
        ...


# ── LLM 交互事件流 ─────────────────────────────────────────────────────────

class LlmEventBus(ABC):
    """LLM 交互事件总线抽象基类 — 由 Host 侧实现并注入 AgentOrchestrator。

    ``emit`` 推送一条 LLM 交互事件（如 tool_call_result）到指定请求的事件流，
    ``cleanup`` 释放该请求的全部事件缓冲与消费者队列；请求上下文
    （set/clear/get_request_context）为线程局部绑定，供工具在执行线程中
    反查当前 request_id。
    """

    @abstractmethod
    def set_request_context(self, request_id: str) -> None:
        """Set the active request_id for this thread."""
        ...

    @abstractmethod
    def clear_request_context(self) -> None:
        """Clear the active request context for this thread."""
        ...

    @abstractmethod
    def get_request_context(self) -> Optional[str]:
        """Get the active request_id for this thread, or None."""
        ...

    @abstractmethod
    def emit(self, request_id: str, event: Dict[str, Any]) -> None:
        """Push one LLM interaction event to ``request_id``'s event stream."""
        ...

    @abstractmethod
    def cleanup(self, request_id: str) -> None:
        """Release all buffered LLM events and consumer queues for ``request_id``."""
        ...


# ── 意图提供 ──────────────────────────────────────────────────────────────

class IntentProvider(ABC):
    """意图提供者抽象基类 — 由 Host 侧实现并注入 AgentOrchestrator。

    提供已注册意图模板的查询与增删改能力；generic 为固定内置意图，
    恒排最前 / 恒可用 / 禁止修改。
    """

    @abstractmethod
    def get_registered_intents(self) -> Dict[str, Any]:
        """返回全部已注册意图（generic 固定内置，恒排最前）。"""
        ...

    @abstractmethod
    def get_intent_info(self, intent_type: str) -> Optional[Dict[str, Any]]:
        """返回单个意图信息；不存在或非法时返回 None。"""
        ...

    @abstractmethod
    def register_intent(self, intent_type: str, name: str, description: str) -> bool:
        """注册新意图模板；generic 固定内置，禁止注册。"""
        ...

    @abstractmethod
    def update_intent(self, intent_type: str, data: Dict[str, Any]) -> bool:
        """合并更新意图配置（仅覆盖传入字段）；generic 禁止更新。"""
        ...

    @abstractmethod
    def delete_intent(self, intent_type: str) -> bool:
        """删除意图；generic 固定兜底，禁止删除。"""
        ...

    @abstractmethod
    def get_available_intents(self) -> Dict[str, Any]:
        """返回启用中的意图（generic 恒可用）。"""
        ...

    @abstractmethod
    def get_enabled_intent_ids(self) -> set:
        """返回启用意图 ID 集合（generic 恒包含）。"""
        ...


# ── 日志输出 ──────────────────────────────────────────────────────────────

class LogSink(ABC):
    """日志记录抽象基类 — Host 侧实现并注入 HelixCore。"""

    @abstractmethod
    def info(self, msg: str) -> None:
        """Record an INFO-level message."""
        ...

    @abstractmethod
    def warning(self, msg: str) -> None:
        """Record a WARNING-level message."""
        ...

    @abstractmethod
    def error(self, msg: str) -> None:
        """Record an ERROR-level message."""
        ...

    @abstractmethod
    def debug(self, msg: str) -> None:
        """Record a DEBUG-level message."""
        ...

    # ── 语义日志（编排器三阶段 / LLM 决策 / 工具调用）──────────────────────

    @abstractmethod
    def orchestrate(self, msg: str) -> None:
        """Record an orchestrator state message."""
        ...

    @abstractmethod
    def llm_decision(self, msg: str) -> None:
        """Record an LLM decision message."""
        ...

    @abstractmethod
    def section(self, msg: str) -> None:
        """Record a section divider for the given title."""
        ...

    @abstractmethod
    def agent_to_llm(self, msg: str) -> None:
        """Record a message sent from the agent to the LLM."""
        ...

    @abstractmethod
    def llm_to_agent(self, msg: str) -> None:
        """Record a message received from the LLM."""
        ...

    @abstractmethod
    def tool_call(self, msg: str) -> None:
        """Record a tool call message."""
        ...


class NullLogSink(LogSink):
    """Silent implementation — used when no sink is injected."""

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass

    def debug(self, msg: str) -> None:
        pass

    def orchestrate(self, msg: str) -> None:
        pass

    def llm_decision(self, msg: str) -> None:
        pass

    def section(self, msg: str) -> None:
        pass

    def agent_to_llm(self, msg: str) -> None:
        pass

    def llm_to_agent(self, msg: str) -> None:
        pass

    def tool_call(self, msg: str) -> None:
        pass
