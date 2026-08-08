"""
HostLogSink — LogSink 抽象基类的 Host 侧实现。

包装 modules.utils.logger 的统一日志输出（颜色/文件），
由组合根实例化后注入 ToolRegistry（tool_registry.set_logger）
与 AgentOrchestrator（log 端口）。
"""

from HelixCore.interface import LogSink
from modules.utils.logger import (
    log_agent_to_llm,
    log_debug,
    log_error,
    log_info,
    log_llm_decision,
    log_llm_to_agent,
    log_orchestrator,
    log_section,
    log_tool_call,
    log_warning,
)


class HostLogSink(LogSink):
    """LogSink 实现 — 转接到统一日志输出（modules.utils.logger）。"""

    def info(self, msg: str) -> None:
        log_info(msg)

    def warning(self, msg: str) -> None:
        log_warning(msg)

    def error(self, msg: str) -> None:
        log_error(msg)

    def debug(self, msg: str) -> None:
        log_debug(msg)

    # ── 语义日志（编排器三阶段 / LLM 决策 / 工具调用）──────────────────────

    def orchestrate(self, msg: str) -> None:
        log_orchestrator(msg)

    def llm_decision(self, msg: str) -> None:
        log_llm_decision(msg)

    def section(self, msg: str) -> None:
        log_section(msg)

    def agent_to_llm(self, msg: str) -> None:
        log_agent_to_llm(msg)

    def llm_to_agent(self, msg: str) -> None:
        log_llm_to_agent(msg)

    def tool_call(self, msg: str) -> None:
        log_tool_call(msg)
