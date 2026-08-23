"""
ChannelRuntime — 每通道私有装配全套 agent 依赖。

每个通道（Web / WeChat / ...）持有独立的一整套运行时：
  - AIEngineBackend   独立 LLM 引擎实例与日志文件（llm_engine_<channel>.log）
  - EventSink         web=SSE 推送；其余通道静默落日志
  - LlmEventBus       每通道独立实例
  - UserQuestionBroker 每通道独立待回答问题表（ask_user 阻塞/应答按通道隔离）
  - IntentStore        每通道独立意图提供者实例
  - ToolRegistry       私有注册表：内部通用工具按共享实例复制 + 本通道
                       三件套工具（ask_user/get_context/clear_context）绑定实例
  - AgentOrchestrator  私有编排器，只可见自己的工具注册表

由此实现工具按通道适配：LLM 在某通道的请求中只能看到并调用该通道注册的
工具实例，提问投递与会话上下文读写均落在该通道自身（ChannelAdapter 的
ask_user / get_context / clear_context 实现）。

组合根（Helix.py）为每个通道调用 build_channel_runtime 完成装配。
"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from HelixCore.interface import EventSink
from HelixCore.tools.base import BaseTool, ToolRegistry
from modules.config.config_manager import ConfigManager
from modules.llm.llm_events import get_request_context
from modules.utils.paths import project_path
from modules.utils.logger import log_info

if TYPE_CHECKING:  # 仅类型标注，避免运行时循环导入
    from modules.channels.base import ChannelAdapter


# ── 事件输出：非 Web 通道的静默实现 ────────────────────────────────────────


class LogEventSink(EventSink):
    """无前端的通道使用 — 不推送状态，仅保留协议实现。"""

    def emit(
        self,
        request_id: str,
        state: Dict[str, Any],
        graph_nodes: Optional[List[Dict[str, Any]]] = None,
        node_result: Optional[Dict[str, Any]] = None,
        completed: bool = False,
    ) -> None:
        pass

    def cleanup(self, request_id: str) -> None:
        pass


# ── 通道三件套工具：BaseTool 绑定通道实例，仅注册进所属通道的私有 registry ──


class ChannelAskUserTool(BaseTool):
    """ask_user — 落到本通道 ChannelAdapter.ask_user。"""

    name = "ask_user"
    description = "当信息不足、存在歧义、需要用户确认时调用该工具向用户提问，禁止自行猜测"
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "需要向用户提问的问题；可在问题中列出选项供用户选择，"
                    "例如：A. xxx，B. xxx"
                ),
            }
        },
        "required": ["question"],
    }

    def __init__(self, channel: "ChannelAdapter"):
        super().__init__()
        self._channel = channel

    def execute(self, question: str = "", **kwargs) -> str:
        request_id = get_request_context()
        if not request_id:
            return "错误: ask_user 需要活跃的请求上下文（request_id），当前无法提问"
        return self._channel.ask_user(request_id, question)


class ChannelGetContextTool(BaseTool):
    """get_context — 落到本通道 ChannelAdapter.get_context。"""

    name = "get_context"
    description = (
        "获取当前会话集中之前已完成请求的上下文。"
        "当用户的请求涉及引用前文结果、基于之前的结果继续操作"
        "（如'将前面的结果发送给某人'、'基于刚才的结果做个总结'）时，"
        "必须先调用此工具获取前文上下文，再据此回答或执行。"
    )
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, channel: "ChannelAdapter"):
        super().__init__()
        self._channel = channel

    def execute(self, **kwargs) -> str:
        return self._channel.get_context()


class ChannelClearContextTool(BaseTool):
    """clear_context — 落到本通道 ChannelAdapter.clear_context。"""

    name = "clear_context"
    description = (
        "清除当前会话集上下文，将之前的所有会话记录归档为历史。"
        "当用户明确要求清除对话上下文、重新开始、忘记之前的内容时调用。"
    )
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, channel: "ChannelAdapter"):
        super().__init__()
        self._channel = channel

    def execute(self, **kwargs) -> str:
        return self._channel.clear_context()


# 全局 registry 中不允许出现的通道工具名（防止旧的全局注册残留遮蔽私有实例）
CHANNEL_TOOL_NAMES = ("ask_user", "get_context", "clear_context")


def build_channel_tool_registry(
    channel: "ChannelAdapter", logger: Any, intent_provider: Any
) -> ToolRegistry:
    """构建通道私有工具注册表。

    - 内部通用工具（plugins + MCP）从全局 registry 以共享实例复制，
      启用状态随装配时快照；
    - 三件套通道工具为本通道绑定的新实例，仅存在于本 registry，
      因此各通道的 LLM 只能触达自己通道的 ask_user / 会话上下文。
    """
    from HelixCore.tools.base import tool_registry as global_tool_registry

    reg = ToolRegistry(logger=logger)
    reg.set_intent_provider(intent_provider)
    for name, tool in global_tool_registry.get_all().items():
        if name in CHANNEL_TOOL_NAMES:
            continue
        reg.register(tool)
    for bound in (
        ChannelAskUserTool(channel),
        ChannelGetContextTool(channel),
        ChannelClearContextTool(channel),
    ):
        reg.register(bound)
    return reg


# ── 每通道独立 LLM 日志文件 ────────────────────────────────────────────────


def _resolve_project_relative(path: str) -> str:
    if not os.path.isabs(path):
        path = project_path(path)
    return path


def resolve_channel_log_file(channel_type: str) -> Optional[str]:
    """解析通道专属 LLM 引擎日志文件路径。

    - web 通道返回 None，沿用全局配置 llm.log_file；
    - 其余通道优先取覆盖项 llm.log_file_<channel_type>，
      未配置时由 llm.log_file 派生 <stem>_<channel_type><ext>
      （默认即 llm_engine_wechat.log）；全局未启用则同样关闭。
    """
    if channel_type == "web":
        return None
    config = ConfigManager()
    override = config.get(f"llm.log_file_{channel_type}", "")
    if override:
        return _resolve_project_relative(override)
    base = config.get("llm.log_file", "")
    if not base:
        return None
    root, ext = os.path.splitext(base)
    return _resolve_project_relative(f"{root}_{channel_type}{ext}")


# ── 通道运行时容器与装配 ───────────────────────────────────────────────────


@dataclass
class ChannelRuntime:
    """一个通道的完整私有 agent 运行时（全部字段按通道独立）。"""

    channel_type: str
    llm_backend: Any
    event_sink: Any
    broker: Any                 # UserQuestionBroker
    intent_provider: Any        # IntentStore
    tool_registry: ToolRegistry
    orchestrator: Any           # AgentOrchestrator


def _build_event_sink(channel_type: str) -> EventSink:
    if channel_type == "web":
        from modules.channels.web.event_sink import SSEEventSink

        return SSEEventSink()
    return LogEventSink()


def build_channel_runtime(channel: "ChannelAdapter") -> ChannelRuntime:
    """为通道装配完整私有运行时并挂到 ``channel.runtime``。

    组合根在每个通道注册后调用一次；此后该通道的所有 agent 请求
    （RPC 经 Web 通道编排器、IM 经各自 worker 线程）都走本套依赖。
    """
    from modules.agent.user_question import UserQuestionBroker
    from modules.host.ai_engine_backend import AIEngineBackend
    from modules.host.config_builder import build_agent_config_from_config_manager
    from modules.host.intent_store import IntentStore
    from modules.host.llm_event_bus import LlmEventBusImpl
    from modules.host.log_sink import HostLogSink
    from HelixCore.orchestrator.orchestrator import AgentOrchestrator

    ctype = channel.channel_type
    log_info(f"[{ctype}] Building channel runtime (private orchestrator + tools)...")

    llm_backend = AIEngineBackend(log_file=resolve_channel_log_file(ctype))
    event_sink = _build_event_sink(ctype)
    broker = UserQuestionBroker()
    intent_provider = IntentStore()
    host_log_sink = HostLogSink()

    tool_registry = build_channel_tool_registry(channel, host_log_sink, intent_provider)
    log_info(
        f"[{ctype}] Channel tool registry ready: "
        f"{len(tool_registry.get_all())} tool(s)"
    )

    orchestrator = AgentOrchestrator(
        llm_backend=llm_backend,
        config=build_agent_config_from_config_manager(),
        event_sink=event_sink,
        intent_provider=intent_provider,
        tool_registry=tool_registry,
        event_bus=LlmEventBusImpl(),
        log=host_log_sink,
        refresh_config=build_agent_config_from_config_manager,
    )

    runtime = ChannelRuntime(
        channel_type=ctype,
        llm_backend=llm_backend,
        event_sink=event_sink,
        broker=broker,
        intent_provider=intent_provider,
        tool_registry=tool_registry,
        orchestrator=orchestrator,
    )
    channel.runtime = runtime
    return runtime
