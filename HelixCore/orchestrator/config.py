"""
AgentConfig — 编排器运行时配置（值对象）。

由 Host 侧在组合根按 Helix.json 构建后注入；HelixCore 不直接读取任何配置源，
配置变更后 Host 侧重建 AgentConfig 并通过 AgentOrchestrator.update_config() 热更新。
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SamplingParams:
    """一次 LLM 调用的采样参数（temperature / top_p）。"""

    temperature: float = 0.2
    top_p: float = 0.9


@dataclass(frozen=True)
class AgentConfig:
    """AgentOrchestrator 运行时配置快照。"""

    # DAG 节点并行执行数 (0/1=串行)
    node_parallel_count: int = 1
    # 执行中任务图最大更新次数
    max_graph_updates: int = 5
    # 规划阶段 ask_user 最大追问轮数
    planning_max_ask_rounds: int = 5
    # 任务规划阶段输入 token 上限（超限报错）
    max_input_tokens: int = 32768

    # 各阶段采样参数
    planning: SamplingParams = field(default_factory=lambda: SamplingParams(0.2, 0.9))
    execution: SamplingParams = field(default_factory=lambda: SamplingParams(0.0, 1.0))
    finalizer: SamplingParams = field(default_factory=lambda: SamplingParams(0.5, 0.9))

    def get_graph_sampling(self, phase: str) -> SamplingParams:
        """按阶段返回采样参数：planning / execution / finalizer（未知阶段回退 planning）。"""
        if phase == "execution":
            return self.execution
        if phase == "finalizer":
            return self.finalizer
        return self.planning
