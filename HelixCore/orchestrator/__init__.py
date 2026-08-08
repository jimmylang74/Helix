"""
HelixCore.orchestrator — 三阶段 DAG 编排器。

- orchestrator.py: AgentOrchestrator（规划 → 节点循环 → 总结）
- agent_state.py:  AgentState（请求状态 TypedDict）
- task_graph.py:   TaskGraph / TaskNode（DAG 任务图状态机）
- config.py:       AgentConfig（运行时配置值对象，Host 注入）
"""
