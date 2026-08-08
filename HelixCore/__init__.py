"""
HelixCore — Agent 核心包（零外部依赖）。

与 Host（modules/host/）的边界由三个注入端口定义：
- LLMBackend     — LLM 调用抽象（Host 侧实现，如 ai_engine 适配器）
- EventSink      — 前端事件输出抽象（Host 侧实现，如 SSE 总线）
- IntentProvider — 意图配置抽象（Host 侧实现，如 Helix.json 读取器）

HelixCore 内允许的唯一共享依赖是 modules.utils.logger。
"""

from HelixCore.ports.events import EventSink
from HelixCore.ports.intents import IntentProvider
from HelixCore.ports.llm import LLMBackend, LLMResponse
from HelixCore.orchestrator.config import AgentConfig, SamplingParams

__all__ = [
    "EventSink",
    "IntentProvider",
    "LLMBackend",
    "LLMResponse",
    "AgentConfig",
    "SamplingParams",
]
