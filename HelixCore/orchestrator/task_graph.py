"""
Task Graph - DAG-based task node graph with state machine.

Node state machine:
  Pending (依赖未满足) → Ready (依赖已满足) → Running (执行中) → Done / Failed

每个 Node 维护独立的 tool-calling 上下文 (node_conversation_history)。
TaskGraph 实例是全局的，多个 Node 共享读视图。
"""

import threading
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class NodeState(Enum):
    PENDING = "Pending"
    READY = "Ready"
    RUNNING = "Running"
    DONE = "Done"
    FAILED = "Failed"


class TaskNode:
    """单个图节点，包含状态、依赖、工具执行上下文。"""

    def __init__(
        self,
        node_id: str,
        title: str,
        depends: Optional[List[str]] = None,
        can_parallel: bool = False,
        initial_tool_calls: Optional[List[Dict[str, Any]]] = None,
    ):
        self.id = node_id
        self.title = title
        self.depends = depends or []
        self.can_parallel = can_parallel
        self.state = NodeState.PENDING
        self.response = ""           # 节点完成后的摘要
        self.reason = ""             # LLM 的分析过程
        self.error: Optional[str] = None
        self.retry_count = 0

        # 独立的 tool-calling 对话历史（LLM ↔ system 往复）
        self.node_conversation_history: List[Dict[str, str]] = []
        # 本节点最近的 tool 执行结果（tool 调用后由 orchestrator 追加）
        self.tool_results: List[Dict[str, Any]] = []
        # planning 阶段预计划的初始 tool calls（执行节点时先直接执行）
        self.initial_tool_calls: List[Dict[str, Any]] = initial_tool_calls or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "initial_tool_calls": list(self.initial_tool_calls),
            "depends": list(self.depends),
            "can_parallel": self.can_parallel,
            "state": self.state.value,
            "response": self.response[:500] if self.response else "",
            "error": self.error or "",
        }


class TaskGraph:
    """DAG 任务图，管理节点状态转换与依赖解析。"""

    def __init__(
        self,
        nodes: Optional[List[Dict[str, Any]]] = None,
        max_graph_updates: int = 5,
    ):
        self._nodes: Dict[str, TaskNode] = {}
        self._lock = threading.Lock()
        self._graph_update_count = 0
        self._max_graph_updates = max_graph_updates
        self._failed_node_ids: Set[str] = set()

        if nodes:
            for n in nodes:
                self.add_node(n)

    # ── 节点管理 ─────────────────────────────────────────────────

    def add_node(self, node_data: Dict[str, Any]) -> str:
        """添加一个新节点。"""
        node_id = node_data.get("id", f"node_{len(self._nodes)}")
        node = TaskNode(
            node_id=node_id,
            title=node_data.get("title", ""),
            depends=node_data.get("depends", []),
            can_parallel=node_data.get("can_parallel", False),
            initial_tool_calls=node_data.get("initial_tool_calls") or [],
        )
        with self._lock:
            self._nodes[node_id] = node
        return node_id

    def update_from_nodes(self, nodes: List[Dict[str, Any]]):
        """
        用 LLM 返回的新节点列表更新图。
        已不存在的旧节点自动标记为 Failed。
        """
        with self._lock:
            self._graph_update_count += 1
            old_ids = set(self._nodes.keys())
            new_ids: Set[str] = set()

            for n in nodes:
                nid = n.get("id", "")
                new_ids.add(nid)
                if nid in self._nodes:
                    existing = self._nodes[nid]
                    existing.title = n.get("title", existing.title)
                    existing.depends = n.get("depends", existing.depends)
                    existing.can_parallel = n.get("can_parallel", existing.can_parallel)
                    if "initial_tool_calls" in n:
                        # 新图为权威：LLM 显式给出的初始工具（含空列表）直接生效，
                        # 避免替代节点沿用旧路径的初始工具
                        existing.initial_tool_calls = n.get("initial_tool_calls") or []
                    # 新图重新包含此前失败的节点 → 视为替代路径，重置后重新执行
                    if existing.state == NodeState.FAILED:
                        existing.state = NodeState.PENDING
                        existing.error = None
                        existing.response = ""
                        existing.tool_results = []
                        existing.retry_count = 0
                        self._failed_node_ids.discard(nid)
                else:
                    self._nodes[nid] = TaskNode(
                        node_id=nid,
                        title=n.get("title", ""),
                        depends=n.get("depends", []),
                        can_parallel=n.get("can_parallel", False),
                        initial_tool_calls=n.get("initial_tool_calls") or [],
                    )

            # 在新图中消失的旧节点 → Failed
            removed = old_ids - new_ids
            for rid in removed:
                if rid in self._nodes:
                    self._nodes[rid].state = NodeState.FAILED
                    self._nodes[rid].error = "Removed by graph update"
                    self._failed_node_ids.add(rid)

    def has_reached_max_updates(self) -> bool:
        return self._graph_update_count >= self._max_graph_updates

    # ── 状态转换 ─────────────────────────────────────────────────

    def get_ready_nodes(self) -> List[TaskNode]:
        """
        找出所有满足依赖条件的节点（PENDING，以及已翻成 READY 但尚未执行的）。
        如果某个依赖 Failed，当前节点也自动 Failed。
        """
        with self._lock:
            ready: List[TaskNode] = []
            for node in self._nodes.values():
                if node.state not in (NodeState.PENDING, NodeState.READY):
                    continue

                # 检查依赖中是否有 Failed
                dep_failed = any(
                    dep_id in self._nodes
                    and self._nodes[dep_id].state == NodeState.FAILED
                    for dep_id in node.depends
                )
                if dep_failed:
                    node.state = NodeState.FAILED
                    node.error = "Dependency node failed"
                    self._failed_node_ids.add(node.id)
                    continue

                # 检查所有依赖是否已完成
                all_deps_done = all(
                    dep_id in self._nodes
                    and self._nodes[dep_id].state == NodeState.DONE
                    for dep_id in node.depends
                )
                if node.depends and not all_deps_done:
                    # READY 节点的依赖可能被图更新改动后不再满足，退回 PENDING 等待
                    if node.state == NodeState.READY:
                        node.state = NodeState.PENDING
                    continue  # 仍有未完成的依赖

                node.state = NodeState.READY
                ready.append(node)
            return ready

    def set_node_running(self, node_id: str):
        with self._lock:
            node = self._nodes.get(node_id)
            if node:
                node.state = NodeState.RUNNING

    def set_node_done(self, node_id: str, response: str = ""):
        with self._lock:
            node = self._nodes.get(node_id)
            if node:
                node.state = NodeState.DONE
                node.response = response

    def set_node_failed(self, node_id: str, error: str = ""):
        with self._lock:
            node = self._nodes.get(node_id)
            if node:
                node.state = NodeState.FAILED
                node.error = error
                self._failed_node_ids.add(node_id)

    def node_exists(self, node_id: str) -> bool:
        with self._lock:
            return node_id in self._nodes

    def get_node(self, node_id: str) -> Optional[TaskNode]:
        with self._lock:
            return self._nodes.get(node_id)

    def is_all_done(self) -> bool:
        """检查是否所有节点已完成（Done 或 Failed）。"""
        with self._lock:
            if not self._nodes:
                return False
            return all(
                n.state in (NodeState.DONE, NodeState.FAILED)
                for n in self._nodes.values()
            )

    def get_running_count(self) -> int:
        with self._lock:
            return sum(1 for n in self._nodes.values() if n.state == NodeState.RUNNING)

    # ── 序列化 ─────────────────────────────────────────────────

    def get_graph_state_string(self, current_node_id: Optional[str] = None) -> str:
        """将图状态序列化为字符串，注入 LLM prompt。"""
        with self._lock:
            lines = ["## Task Graph Status"]
            for node in self._nodes.values():
                marker = " ▶" if node.id == current_node_id else ""
                dep_str = (
                    f"depends_on={node.depends}"
                    if node.depends
                    else "no_deps"
                )
                lines.append(
                    f"- [{node.state.value}]{marker} {node.id}: {node.title} ({dep_str})"
                )
                if node.response:
                    lines.append(f"  Result: {node.response[:200]}")
                if node.error:
                    lines.append(f"  Error: {node.error}")
            lines.append(
                f"\nGraph update count: {self._graph_update_count}/{self._max_graph_updates}"
            )
            if self._failed_node_ids:
                lines.append(
                    f"Failed paths (avoid): {', '.join(sorted(self._failed_node_ids))}"
                )
            return "\n".join(lines)

    def to_dict_list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [n.to_dict() for n in self._nodes.values()]

    def get_summary_for_finalizer(self) -> str:
        """为 finalizer 阶段生成所有节点的完成摘要。"""
        with self._lock:
            lines = ["## Task Execution Summary"]
            for node in self._nodes.values():
                status_icon = {
                    NodeState.DONE: "✅",
                    NodeState.FAILED: "❌",
                    NodeState.PENDING: "⬜",
                    NodeState.READY: "🔄",
                    NodeState.RUNNING: "▶",
                }.get(node.state, "⬜")
                lines.append(f"\n{status_icon} [{node.state.value}] {node.id}: {node.title}")
                if node.response:
                    lines.append(f"   Result: {node.response}")
                if node.error:
                    lines.append(f"   Error: {node.error}")
            return "\n".join(lines)
