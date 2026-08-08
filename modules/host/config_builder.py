"""
Host 侧配置装配 — 从 Helix.json 构建 HelixCore 的 AgentConfig 快照。

HelixCore 只定义 AgentConfig / SamplingParams 值对象；实际读取
Helix.json 的动作由本模块完成，组合根与编排器热重载（update_config）
均调用本函数。
"""

from HelixCore.orchestrator.config import AgentConfig, SamplingParams
from modules.config.config_manager import ConfigManager


def build_agent_config_from_config_manager() -> AgentConfig:
    """Build an AgentConfig snapshot from Helix.json (host-side read)."""
    cm = ConfigManager()
    sampling = {
        phase: cm.get_graph_sampling(phase)
        for phase in ("planning", "execution", "finalizer")
    }
    return AgentConfig(
        node_parallel_count=int(cm.get("server.node_parallel_count", 1)),
        max_graph_updates=int(cm.get("llm.max_graph_updates", 5)),
        planning_max_ask_rounds=int(cm.get("llm.planning_max_ask_rounds", 5)),
        max_input_tokens=int(cm.get("llm.max_input_tokens", 32768)),
        planning=SamplingParams(**sampling["planning"]),
        execution=SamplingParams(**sampling["execution"]),
        finalizer=SamplingParams(**sampling["finalizer"]),
    )
