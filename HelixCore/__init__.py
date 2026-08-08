"""
HelixCore — Agent 核心包（零外部依赖）。

与 Host（modules/host/）的边界由四个注入端口定义，均为抽象基类（ABC），统一收敛于 HelixCore.interface：
- LLMBackend     — LLM 调用抽象（Host 侧实现，如 ai_engine 适配器）
- EventSink      — 前端事件输出抽象（Host 侧实现，如 SSE 总线）
- IntentProvider — 意图配置抽象（Host 侧实现，如 Helix.json 读取器）
- LogSink        — 日志输出抽象（Host 侧实现，如统一日志收口）

HelixCore 不 import 任何 Host 模块，仅使用标准库；全部依赖由组合根
显式构造后注入。
"""

from HelixCore.interface import (
    EventSink,
    IntentProvider,
    LLMBackend,
    LLMResponse,
    LogSink,
    NullLogSink,
)
from HelixCore.orchestrator.config import AgentConfig, SamplingParams
from HelixCore.tools.base import BaseTool, ToolRegistry

__all__ = [
    "EventSink",
    "IntentProvider",
    "LLMBackend",
    "LLMResponse",
    "LogSink",
    "NullLogSink",
    "AgentConfig",
    "SamplingParams",
    "BaseTool",
    "ToolRegistry",
]
