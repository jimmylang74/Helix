"""
Agent Orchestrator — Three-phase LLM call chain architecture.

Phase 1: _task_planning()      LLM 将用户请求分解为 DAG 节点图
Phase 2: _task_graph_node_loop() 按依赖顺序执行所有节点（支持并行）
Phase 3: _task_finalizer()    可选：将所有节点结果汇总

Option B — 同一时刻只有一个 Running 节点 emit 流式事件到前端，
其余并行节点静默执行，节点完成后 emit 一条结构化完成事件。
"""

import json
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from modules.core.agent_state import (
    AgentState, create_initial_state
)
from modules.core.task_graph import TaskGraph, NodeState
from modules.agents.tool_base import tool_registry
from modules.llm.llm_client import LLMClient, LLMResponse, ToolDefinition
from modules.prompts.task_graph_prompts import (
    SYSTEM_PROMPT_TASK_PLANNING,
    USER_PROMPT_TASK_PLANNING,
    SYSTEM_PROMPT_FINALIZER,
    USER_PROMPT_FINALIZER,
    get_node_system_prompt,
    USER_PROMPT_NODE_EXECUTION,
)
from modules.prompts.ppt_prompts import DOMAIN_SECTION_PPT
from modules.prompts.research_prompts import DOMAIN_SECTION_RESEARCH
from modules.prompts.coding_prompts import DOMAIN_SECTION_CODING
from modules.prompts.common_prompt import ASK_USER_RULES, COMMON_JSON_CONTRACT
from modules.utils.logger import (
    log_orchestrator, log_agent_action, log_llm_decision,
    log_error, log_info, log_section, log_agent_to_llm, log_llm_to_agent, log_tool_call
)
from modules.llm.llm_events import (
    set_request_context, clear_request_context,
    cleanup as llm_cleanup, emit as _emit_llm_event, get_request_context
)
from modules.core import status_events
from modules.core.user_question import user_question_broker
from modules.utils.file_ops import FileOps
from modules.utils import history_store
from modules.config.config_manager import ConfigManager
from modules.agent.tokenizer import TokenEstimator, create_estimator_for_config


class AgentOrchestrator:
    """Three-phase orchestrator with DAG task graph."""

    def __init__(self):
        self.llm = LLMClient()
        self.file_ops = FileOps()
        self._active_states: Dict[str, AgentState] = {}
        self._states_lock = threading.Lock()
        self._graphs: Dict[str, TaskGraph] = {}
        self._graphs_lock = threading.Lock()
        # Option B: which node is currently allowed to stream to frontend
        self._stream_node_id: Dict[str, str] = {}
        self._stream_lock = threading.Lock()
        # Per-request token estimators (created via create_estimator_for_config)
        self._estimators: Dict[str, TokenEstimator] = {}
        self._estimators_lock = threading.Lock()
        self._usage_lock = threading.Lock()

    # ═══════════════════════════════════════════════════════════════
    # Public API (backward-compatible)
    # ═══════════════════════════════════════════════════════════════

    def process_request(
        self,
        user_request: str,
        request_id: Optional[str] = None,
        forced_intent: str = "auto",
    ) -> Dict[str, Any]:
        """Main entry point — process a user request end-to-end."""
        if not request_id:
            request_id = f"req_{uuid.uuid4().hex[:12]}"

        set_request_context(request_id)
        log_section(f"Processing Request: {request_id}")
        log_info(f"User request: {user_request[:200]}")

        state = create_initial_state(user_request, request_id)
        state["forced_intent"] = forced_intent
        with self._states_lock:
            self._active_states[request_id] = state
        created_at = datetime.now().isoformat(timespec="seconds")

        try:
            # ── Phase 1: Task Planning ────────────────────────────
            planning_result = self._task_planning(state)
            if state.get("error"):
                return self._error_response(state)

            if planning_result.get("task_complete"):
                # 直接回答，不需要节点执行
                state["final_result"] = planning_result.get("response", "")
                state["orchestrator_phase"] = "done"
                status_events.emit(request_id, state, completed=True)
                log_section(f"Request completed (direct): {request_id}")
                return self._build_success_result(state)

            # ── Phase 2: Task Graph Node Loop ─────────────────────
            self._task_graph_node_loop(state)

            # ── Phase 3: Finalizer (optional) ─────────────────────
            if planning_result.get("need_finalizer", True):
                self._task_finalizer(state)
            else:
                # 不需要 finalizer，从节点结果拼接最终结果
                state["final_result"] = self._collect_node_results(state)

            state["orchestrator_phase"] = "done"
            # 推送最终结果（finalizer 的 final_answer 或拼接结果）到前端
            graph = self._get_graph(request_id)
            graph_nodes = graph.to_dict_list() if graph else None
            status_events.emit(request_id, state, graph_nodes=graph_nodes, completed=True)
            result = self._build_success_result(state)
            log_section(f"Request completed: {request_id}")
            return result

        except Exception as e:
            log_error(f"Orchestrator error: {e}")
            import traceback
            log_error(traceback.format_exc())
            state["error"] = str(e)
            state["orchestrator_phase"] = "done"
            graph = self._get_graph(request_id)
            graph_nodes = graph.to_dict_list() if graph else None
            status_events.emit(request_id, state, graph_nodes=graph_nodes)
            return self._error_response(state, str(e))
        finally:
            # 任务结束后持久化一条历史记录 (成功/失败/取消全覆盖), 供"使用记录"页面展示
            history_store.record({
                "request_id": request_id,
                "user_request": user_request,
                "intent_type": state.get("intent_type", ""),
                "success": not state.get("error") and not state.get("cancelled"),
                "created_at": created_at,
                "error": state.get("error"),
                "generated_files": state.get("generated_files", []),
            })
            llm_cleanup(request_id)
            # 唤醒可能仍在等待用户回答的 ask_user，避免节点线程阻塞到超时
            user_question_broker.cancel(request_id)
            # 任务结束即释放：前端快速测试页改走"不销毁"方案，无需为已完成任务保留状态与缓冲
            with self._states_lock:
                self._active_states.pop(request_id, None)
            status_events.cleanup(request_id)
            with self._graphs_lock:
                self._graphs.pop(request_id, None)
            with self._stream_lock:
                self._stream_node_id.pop(request_id, None)
            with self._estimators_lock:
                self._estimators.pop(request_id, None)
            clear_request_context()

    def cancel_request(self, request_id: str) -> bool:
        """Cancel an active request."""
        state = self._active_states.get(request_id)
        if not state:
            return False
        state["cancelled"] = True
        state["orchestrator_phase"] = "done"
        state["error"] = "Cancelled by user"
        # 唤醒等待用户回答的 ask_user，让节点线程退出阻塞
        user_question_broker.cancel(request_id)
        log_orchestrator(f"Request {request_id} cancelled by user")
        graph = self._get_graph(request_id)
        graph_nodes = graph.to_dict_list() if graph else None
        status_events.emit(request_id, state, graph_nodes=graph_nodes)
        return True

    def is_cancelled(self, state: AgentState) -> bool:
        return state.get("cancelled", False)

    def get_state(self, request_id: str) -> Optional[Dict[str, Any]]:
        state = self._active_states.get(request_id)
        if not state:
            return None

        graph = self._get_graph(request_id)
        return {
            "request_id": request_id,
            "intent_type": state.get("intent_type"),
            "orchestrator_phase": state.get("orchestrator_phase"),
            "task_graph_nodes": graph.to_dict_list() if graph else [],
            "all_done": graph.is_all_done() if graph else False,
            "final_result": state.get("final_result", ""),
            "generated_files": state.get("generated_files", []),
            "token_usage": state.get("token_usage"),
            "error": state.get("error"),
            "cancelled": state.get("cancelled", False),
        }

    def refresh_llm(self):
        """Refresh LLM client (call after config change)."""
        self.llm.refresh()

    # ═══════════════════════════════════════════════════════════════
    # Phase 1 — Task Planning
    # ═══════════════════════════════════════════════════════════════

    def _task_planning(self, state: AgentState) -> Dict[str, Any]:
        """Phase 1: LLM 分解任务为 DAG 节点图。"""
        state["orchestrator_phase"] = "planning"
        log_section("Phase 1: Task Planning")

        # 注入可用工具列表，让 LLM 在拆分节点时只选择真实存在的工具
        tool_definitions = self._build_tool_definitions()
        tools_section = LLMClient.format_tools_section(tool_definitions)

        forced_intent = state.get("forced_intent", "auto")
        if forced_intent != "auto":
            # 已知 intent，直接用对应 system prompt 规划
            system_prompt = self._get_system_prompt(forced_intent)
            log_agent_to_llm(f"Forced intent={forced_intent}, planning task graph...")
        else:
            # 规划 system prompt 内含 JSON 示例的裸花括号，不能用 .format()，用占位符替换
            system_prompt = SYSTEM_PROMPT_TASK_PLANNING.replace(
                "{ask_user_rules}", ASK_USER_RULES
            )
            log_agent_to_llm("Sending request to LLM for task planning...")

        # 规划阶段提问：LLM 无法规划时经顶层 tools 调用 ask_user，回答追加进上下文重新规划（上限 llm.planning_max_ask_rounds 轮）
        max_ask_rounds = ConfigManager().get("llm.planning_max_ask_rounds", 5)
        planning_sampling = ConfigManager().get_graph_sampling("planning")
        planning_context = state["user_request"]
        for ask_round in range(max_ask_rounds + 1):
            user_prompt = USER_PROMPT_TASK_PLANNING.format(
                user_request=planning_context,
                json_contract=COMMON_JSON_CONTRACT,
            )

            # 检查 token 预算
            if not self._check_token_budget(
                state["request_id"], system_prompt, user_prompt, tools_section
            ):
                state["error"] = "Prompt exceeds max_input_tokens limit at planning phase"
                log_error(state["error"])
                return {"task_complete": True, "response": state["error"]}

            response = self.llm.ask_json(
                prompt=user_prompt,
                system_prompt=system_prompt,
                tools=tool_definitions,
                temperature=planning_sampling["temperature"],
                top_p=planning_sampling["top_p"],
            )

            self._record_usage(
                state,
                f"{system_prompt}\n\n{tools_section}\n\n{user_prompt}",
                json.dumps(response, ensure_ascii=False),
            )

            ask_tools = [
                t for t in (response.get("tools") or [])
                if isinstance(t, dict) and t.get("name") == "ask_user"
            ]
            if ask_tools and ask_round < max_ask_rounds:
                for i, tc in enumerate(ask_tools):
                    fake_tc = {
                        "name": "ask_user",
                        "arguments": tc.get("arguments", {}),
                        "id": f"planning_ask_{ask_round}_{i}",
                    }
                    result = self._execute_tool_call(state, fake_tc)
                    planning_context = (
                        f"{planning_context}\n\n[规划阶段补充信息]\n{result}"
                    )
                log_llm_decision(
                    f"Planning asked user {len(ask_tools)} question(s), "
                    "re-planning with answers..."
                )
                continue
            break

        # 解析结果
        if forced_intent != "auto":
            intent_type = forced_intent
        else:
            intent_type = response.get("intent_type", "research")
            if intent_type not in ("ppt", "research", "coding"):
                intent_type = "research"

        state["intent_type"] = intent_type
        log_llm_decision(f"Intent: {intent_type}")

        task_complete = response.get("task_complete", False)
        if task_complete:
            direct_response = response.get("response", "")
            log_llm_decision(f"Task complete (direct): {direct_response[:100]}")
            state["final_result"] = direct_response
            return {
                "task_complete": True,
                "response": direct_response,
                "intent_type": intent_type,
                "need_finalizer": False,
            }

        # 创建 TaskGraph
        nodes = response.get("task_graph_nodes", [])
        if not nodes:
            # 保底：创建一个默认节点
            nodes = [{
                "id": "node_1",
                "title": f"Process: {state['user_request'][:100]}",
                "tools": [],
                "depends": [],
                "can_parallel": False,
            }]

        max_updates = ConfigManager().get("llm.max_graph_updates", 5)
        graph = TaskGraph(nodes=nodes, max_graph_updates=max_updates)

        with self._graphs_lock:
            self._graphs[state["request_id"]] = graph

        need_finalizer = response.get("need_finalizer", True)
        reason = response.get("reason", "")

        log_llm_decision(
            f"Graph created: {len(nodes)} nodes, "
            f"need_finalizer={need_finalizer}, reason={reason[:100]}"
        )

        graph_nodes = graph.to_dict_list()
        status_events.emit(state["request_id"], state, graph_nodes=graph_nodes)

        return {
            "task_complete": False,
            "intent_type": intent_type,
            "need_finalizer": need_finalizer,
            "reason": reason,
        }

    # ═══════════════════════════════════════════════════════════════
    # Phase 2 — Task Graph Node Loop
    # ═══════════════════════════════════════════════════════════════

    def _task_graph_node_loop(self, state: AgentState):
        """Phase 2: 循环执行 DAG 节点，直到全部完成。"""
        state["orchestrator_phase"] = "node_loop"
        log_section("Phase 2: Task Graph Node Loop")

        graph = self._get_graph(state["request_id"])
        if not graph:
            state["error"] = "No task graph found"
            return

        parallel_count = ConfigManager().get("server.node_parallel_count", 1)

        while not graph.is_all_done():
            if self.is_cancelled(state):
                log_orchestrator("Node loop cancelled by user")
                break

            # 找出所有 Ready 节点
            ready_nodes = graph.get_ready_nodes()
            if not ready_nodes:
                # 没有 Ready 节点但也没全部完成 → 检查是否卡死
                if graph.get_running_count() == 0:
                    log_orchestrator("No ready or running nodes — graph may be stuck or all done")
                    break
                # 有 Running 但还没 Ready → 等下一个循环
                log_orchestrator(f"Waiting for {graph.get_running_count()} running node(s)...")
                status_events.emit(state["request_id"], state, graph_nodes=graph.to_dict_list())
                # 没有 Ready 节点会无限空转，加个安全等待
                import time
                time.sleep(0.1)
                continue

            # 根据并行配置选择执行节点
            nodes_to_run = ready_nodes[:parallel_count]

            # 对没有 can_parallel 标记的，只跑一个
            all_non_parallel = all(not n.can_parallel for n in nodes_to_run)
            if all_non_parallel and len(nodes_to_run) > 1:
                nodes_to_run = nodes_to_run[:1]

            log_orchestrator(
                f"Running {len(nodes_to_run)} node(s): "
                + ", ".join(f"{n.id}[{n.title[:30]}]" for n in nodes_to_run)
            )

            # 串行执行或启动线程执行
            if len(nodes_to_run) == 1:
                node = nodes_to_run[0]
                self._set_stream_node(state["request_id"], node.id)
                graph.set_node_running(node.id)
                status_events.emit(state["request_id"], state, graph_nodes=graph.to_dict_list())
                self._execute_node(state, node)
            else:
                threads = []
                for node in nodes_to_run:
                    graph.set_node_running(node.id)

                # 第一个节点流式，其他静默
                nodes_to_run[0].can_parallel = True  # 确保被标记
                self._set_stream_node(state["request_id"], nodes_to_run[0].id)
                status_events.emit(state["request_id"], state, graph_nodes=graph.to_dict_list())

                for i, node in enumerate(nodes_to_run):
                    is_stream = (i == 0)
                    t = threading.Thread(
                        target=self._execute_node,
                        args=(state, node),
                        kwargs={"emit_stream": is_stream},
                        daemon=True,
                    )
                    threads.append(t)
                    t.start()

                for t in threads:
                    t.join()

            self._clear_stream_node(state["request_id"])
            status_events.emit(state["request_id"], state, graph_nodes=graph.to_dict_list())

        log_orchestrator("Task Graph Node Loop completed.")

    def _execute_node(
        self,
        state: AgentState,
        node: Any,
        emit_stream: bool = True,
    ):
        """执行单个节点（内部 tool-calling 循环）。"""
        request_id = state["request_id"]
        # 并行节点线程没有外层 process_request 设置的上下文，这里补设，
        # 使工具（如 ask_user）能通过 get_request_context 拿到 request_id
        set_request_context(request_id)
        tool_definitions = self._build_tool_definitions()
        system_prompt = get_node_system_prompt(state["intent_type"])
        execution_sampling = ConfigManager().get_graph_sampling("execution")
        # ask_with_tools 会把 tools 段追加进 system prompt 一起发送（见
        # llm_client.ask_with_tools），预算检查必须把这段计入输入 token。
        tools_section = LLMClient.format_tools_section(
            tool_definitions,
            heading=(
                "Available tools (respond with JSON that includes "
                "tool_calls if needed):"
            ),
        )

        # 重置节点的 conversation history
        node.node_conversation_history = []

        # planning 阶段预计划的初始 tool calls：直接执行，再进入 LLM 迭代
        if node.initial_tool_calls and not node.tool_results:
            for i, tc in enumerate(node.initial_tool_calls):
                name = tc.get("name", "")
                arguments = tc.get("arguments", {})
                fake_tc = dict(tc)
                fake_tc.setdefault("id", f"node_{node.id}_pre{i}")
                result = self._execute_tool_call(state, fake_tc)
                node.tool_results.append({
                    "tool": name,
                    "arguments": arguments,
                    "result": result[:1000] if result else "",
                })
            log_orchestrator(
                f"Node {node.id}: executed {len(node.initial_tool_calls)} "
                "pre-planned tool call(s)"
            )

        iteration = 0
        while True:
            iteration += 1
            if self.is_cancelled(state):
                break

            graph = self._get_graph(request_id)
            if not graph:
                break

            # 构造 prompt
            graph_state_str = graph.get_graph_state_string(node.id)
            conv = self._format_node_conversation(node)
            tool_results_str = self._format_tool_results(node)

            user_prompt = USER_PROMPT_NODE_EXECUTION.format(
                node_id=node.id,
                node_title=node.title,
                node_tools=", ".join(node.tools) if node.tools else "(no suggestion)",
                tool_results=tool_results_str,
                conversation_history=conv,
                graph_state=graph_state_str,
                json_contract=COMMON_JSON_CONTRACT,
            )

            # 检查 token 预算：system_prompt(+tools_section) + 完整 user_prompt
            if not self._check_token_budget(
                request_id,
                system_prompt,
                tools_section,
                user_prompt,
            ):
                err = f"Node {node.id}: prompt exceeds max_input_tokens limit"
                log_error(err)
                graph.set_node_failed(node.id, err)
                break

            log_agent_to_llm(
                f"Node {node.id} iter {iteration}: calling LLM "
                f"{'(streaming)' if emit_stream else '(silent)'}..."
            )

            # 调用 LLM（带工具）
            llm_response = self.llm.ask_with_tools(
                prompt=user_prompt,
                tools=tool_definitions,
                system_prompt=system_prompt,
                emit_stream=emit_stream,
                temperature=execution_sampling["temperature"],
                top_p=execution_sampling["top_p"],
            )

            # 统计该节点 LLM 调用的输入/输出 token
            self._record_usage(
                state,
                f"{system_prompt}\n\n{user_prompt}",
                llm_response.content,
            )

            # 解析响应
            response_data = self._parse_llm_response(llm_response.content)
            node.node_conversation_history.append({
                "role": "assistant",
                "content": llm_response.content,
            })

            # 检查 tool_calls（LLM 可能通过 OpenAI 原生 tool calling 返回）
            tool_calls = llm_response.tool_calls or response_data.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "")
                    arguments = tc.get("arguments", {})
                    result = self._execute_tool_call(state, tc)
                    node.tool_results.append({
                        "tool": name,
                        "arguments": arguments,
                        "result": result[:1000] if result else "",
                    })
                continue

            # 通过 JSON 协议工具调用
            json_tools = response_data.get("tools", [])
            if json_tools:
                # 批量执行所有工具
                for tc in json_tools:
                    name = tc.get("name", "")
                    arguments = tc.get("arguments", {})
                    fake_tc = {"name": name, "arguments": arguments, "id": f"node_{node.id}_t{iteration}_{len(node.tool_results)}"}
                    result = self._execute_tool_call(state, fake_tc)
                    node.tool_results.append({
                        "tool": name,
                        "arguments": arguments,
                        "result": result[:1000] if result else "",
                    })
                continue

            # 判断节点完成情况
            node_complete = response_data.get("node_complete", False)
            node_response = response_data.get("response", "")

            if node_complete:
                node.reason = response_data.get("reason", "")
                graph.set_node_done(node.id, node_response)
                log_llm_decision(
                    f"Node {node.id} completed: {node_response[:100]}"
                )
                # 节点结果（LLM response 字段）推送到前端"最终结果"窗口
                status_events.emit(
                    request_id,
                    state,
                    graph_nodes=graph.to_dict_list(),
                    node_result={
                        "node_id": node.id,
                        "node_title": node.title,
                        "response": node_response,
                    },
                )
                break

            # 检查是否需要更新图
            need_update = response_data.get("need_update_node", False)
            if need_update:
                new_nodes = response_data.get("task_graph_nodes", [])
                if new_nodes and not graph.has_reached_max_updates():
                    log_llm_decision(
                        f"Node {node.id}: updating graph with {len(new_nodes)} nodes"
                    )
                    graph.update_from_nodes(new_nodes)

                    # 检查当前节点在新图中是否还存在
                    # update_from_nodes 将消失的节点设为 FAILED，但仍保留在 _nodes 中
                    current = graph.get_node(node.id)
                    if current is None or current.state == NodeState.FAILED:
                        if current is None:
                            graph.set_node_failed(
                                node.id,
                                "Node removed during graph update"
                            )
                        log_orchestrator(
                            f"Node {node.id} removed by graph update → Failed"
                        )
                        break
                elif not new_nodes:
                    log_error(
                        f"Node {node.id}: need_update_node=true but no nodes provided"
                    )
                    graph.set_node_failed(node.id, "Graph update with no nodes")
                    break
                else:
                    log_error(f"Node {node.id}: max graph updates reached")
                    graph.set_node_failed(node.id, "Max graph updates reached")
                    break

            # 既没有 tools、node_complete、need_update_node → 视为失败
            log_error(
                f"Node {node.id} iter {iteration}: no tools/complete/update "
                f"— marking failed"
            )
            graph.set_node_failed(
                node.id,
                "No tool calls or completion from LLM"
            )
            break

    # ═══════════════════════════════════════════════════════════════
    # Phase 3 — Finalizer
    # ═══════════════════════════════════════════════════════════════

    def _task_finalizer(self, state: AgentState):
        """Phase 3: 将所有节点结果汇总为最终答案。"""
        state["orchestrator_phase"] = "finalizing"
        log_section("Phase 3: Finalizer")

        graph = self._get_graph(state["request_id"])
        if not graph:
            state["final_result"] = self._collect_node_results(state)
            return

        graph_summary = graph.get_summary_for_finalizer()
        user_prompt = USER_PROMPT_FINALIZER.format(
            user_request=state["user_request"],
            graph_summary=graph_summary,
            json_contract=COMMON_JSON_CONTRACT,
        )

        if not self._check_token_budget(
            state["request_id"], SYSTEM_PROMPT_FINALIZER, user_prompt
        ):
            log_error("Finalizer prompt exceeds max_input_tokens limit, using raw results")
            state["final_result"] = self._collect_node_results(state)
            return

        log_agent_to_llm("Requesting final summary from LLM...")
        finalizer_sampling = ConfigManager().get_graph_sampling("finalizer")
        response = self.llm.ask_json(
            prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT_FINALIZER,
            temperature=finalizer_sampling["temperature"],
            top_p=finalizer_sampling["top_p"],
        )

        self._record_usage(
            state,
            f"{SYSTEM_PROMPT_FINALIZER}\n\n{user_prompt}",
            json.dumps(response, ensure_ascii=False),
        )

        final_answer = response.get("final_answer", response.get("response", ""))
        state["final_result"] = final_answer or self._collect_node_results(state)
        log_llm_decision(f"Final summary generated ({len(state['final_result'])} chars)")

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _get_graph(self, request_id: str) -> Optional[TaskGraph]:
        with self._graphs_lock:
            return self._graphs.get(request_id)

    def _set_stream_node(self, request_id: str, node_id: str):
        with self._stream_lock:
            self._stream_node_id[request_id] = node_id

    def _clear_stream_node(self, request_id: str):
        with self._stream_lock:
            self._stream_node_id.pop(request_id, None)

    def _get_system_prompt(self, intent_type: str) -> str:
        domain_sections = {
            "ppt": DOMAIN_SECTION_PPT,
            "research": DOMAIN_SECTION_RESEARCH,
            "coding": DOMAIN_SECTION_CODING,
        }
        base = SYSTEM_PROMPT_TASK_PLANNING.replace(
            "{ask_user_rules}", ASK_USER_RULES
        )
        domain = domain_sections.get(intent_type, "")
        if not domain:
            return base
        return f"{base}\n\n{domain}"

    def _build_tool_definitions(self) -> List[ToolDefinition]:
        tool_definitions = []
        for tool in tool_registry.get_enabled_tools():
            tool_definitions.append(ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            ))
        return tool_definitions

    def _create_estimator(self):
        """Build the token estimator for the current LLM config.

        Orchestrator LLM calls stream through ai_engine, so
        create_estimator_for_config is called with streaming=True (for
        Ollama this picks the HF → tiktoken → simple chain, since litellm
        drops usage on the stream path).
        """
        llm_config = self.llm.config.get_llm_config()
        return create_estimator_for_config(
            provider=llm_config.get("provider", "ollama_native"),
            model=llm_config.get("model", "qwen2.5:7b"),
            streaming=True,
        )

    def _get_estimator(self, request_id: str):
        with self._estimators_lock:
            estimator = self._estimators.get(request_id)
        if estimator is None:
            estimator = self._create_estimator()
            with self._estimators_lock:
                self._estimators[request_id] = estimator
        return estimator

    def _record_usage(self, state: AgentState, input_text: str, output_text: str):
        """Estimate one LLM call's input/output tokens and accumulate totals.

        Updates ``state["token_usage"]`` with the last call's counts
        (current) and the request-wide sums (total); the next
        ``status_events.emit`` carries them to the frontend.
        """
        estimator = self._get_estimator(state["request_id"])
        usage = estimator.estimate_usage(input_text, output_text)
        usage_state = state.setdefault(
            "token_usage",
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "tokenizer": "",
            },
        )
        with self._usage_lock:
            usage_state["input_tokens"] = usage.input_tokens
            usage_state["output_tokens"] = usage.output_tokens
            usage_state["total_input_tokens"] = (
                usage_state.get("total_input_tokens", 0) + usage.input_tokens
            )
            usage_state["total_output_tokens"] = (
                usage_state.get("total_output_tokens", 0) + usage.output_tokens
            )
            usage_state["tokenizer"] = estimator.__class__.__name__

    def _check_token_budget(self, request_id: str, *text_parts: str) -> bool:
        """用 tokenizer 计算输入 token 是否超过限制。超过返回 False。

        使用当前请求的 token 估算器（按 LLM 配置经 create_estimator_for_config
        选择：HF tokenizer → tiktoken → 字节粗估），与 _record_usage 的统计口径一致。
        """
        max_tokens = ConfigManager().get("llm.max_input_tokens", 32768)
        estimator = self._get_estimator(request_id)
        estimated = sum(estimator.count_tokens(p) for p in text_parts)
        if estimated > max_tokens:
            log_error(
                f"Token budget exceeded: ~{estimated} estimated "
                f"({estimator.__class__.__name__}) > {max_tokens} max"
            )
            return False
        return True

    def _parse_llm_response(self, content: str) -> Dict[str, Any]:
        import json_repair

        # 1. 严格解析
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # 2. json-repair 兜底
        try:
            parsed = json_repair.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        log_orchestrator(
            f"_parse_llm_response: FALLBACK, content_len={len(content)}"
        )
        return {"response": content}

    def _execute_tool_call(
        self,
        state: AgentState,
        tool_call: Dict[str, Any],
    ) -> str:
        """执行单个工具调用，返回结果文本。"""
        name = tool_call.get("name", "")
        arguments = tool_call.get("arguments", {})
        tc_id = tool_call.get("id", "")
        log_tool_call(
            f"Executing tool: {name}({json.dumps(arguments, ensure_ascii=False)})"
        )

        result_text = ""
        try:
            # 特殊处理 web_search / image_search（保持向后兼容）
            if name == "web_search":
                query = arguments.get("query", "")
                result_text = tool_registry.call_tool(
                    "web_search", {"query": query}
                )
                results = json.loads(result_text) if result_text else []
                urls = [r["url"] for r in results if r.get("url")]
                state["urls_to_fetch"] = urls
                self._emit_tool_result(tc_id, name, f"搜索到 {len(results)} 条结果")
            elif name == "image_search":
                query = arguments.get("query", "")
                max_results = arguments.get("max_results", 5)
                result_text = tool_registry.call_tool(
                    "image_search",
                    {"query": query, "max_results": max_results},
                )
                results = json.loads(result_text) if result_text else []
                urls = [r["url"] for r in results if r.get("url")]
                state["urls_to_fetch"].extend(urls)
                self._emit_tool_result(
                    tc_id, name, f"搜索到 {len(results)} 张图片"
                )
                if state.get("intent_type") == "ppt" and urls:
                    log_agent_action(f"Auto-downloading {len(urls)} images...")
                    saved = tool_registry.call_tool("image_download", {"urls": urls})
                    state["generated_files"].extend(saved)
            else:
                result_text = tool_registry.call_tool(name, arguments)
                preview = (
                    result_text[:500] + "..."
                    if len(result_text) > 500
                    else result_text
                )
                self._emit_tool_result(tc_id, name, preview)
        except Exception as e:
            log_error(f"Tool execution failed: {name}: {e}")
            self._emit_tool_result(tc_id, name, f"错误: {e}")
            result_text = f"Error: {e}"

        return result_text

    def _emit_tool_result(
        self, tc_id: str, name: str, result_preview: str
    ):
        """Emit a tool_call_result event to the SSE stream."""
        request_id = get_request_context()
        if request_id:
            _emit_llm_event(request_id, {
                "type": "tool_call_result",
                "id": tc_id,
                "name": name,
                "result": result_preview,
            })

    def _format_node_conversation(self, node: Any) -> str:
        """格式化节点的 conversation history 用于 prompt。"""
        history = getattr(node, "node_conversation_history", [])
        if not history:
            return "(no conversation yet)"
        parts = []
        for msg in history[-6:]:  # 只保留最近 6 条
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            parts.append(f"[{role}]: {content[:800]}")
        return "\n\n".join(parts)

    def _format_tool_results(self, node: Any) -> str:
        """格式化节点上最近的 tool 执行结果。"""
        results = getattr(node, "tool_results", [])
        if not results:
            return "(no tool results yet)"
        parts = []
        for r in results[-10:]:  # 最近 10 条
            tool_name = r.get("tool", "?")
            result = r.get("result", "")
            parts.append(f"[{tool_name}]: {result[:500]}")
        return "\n\n".join(parts)

    def _collect_node_results(self, state: AgentState) -> str:
        """当不需要 finalizer 时，从图节点收集结果。"""
        graph = self._get_graph(state["request_id"])
        if not graph:
            return ""
        lines = []
        for node in graph.to_dict_list():
            status = node.get("state", "")
            resp = node.get("response", "")
            err = node.get("error", "")
            if resp:
                lines.append(f"## {node.get('title', '')}\n{resp}")
            if err:
                lines.append(f"## {node.get('title', '')} (Failed)\n{err}")
        return "\n\n".join(lines)

    def _build_success_result(self, state: AgentState) -> Dict[str, Any]:
        return {
            "success": True,
            "request_id": state.get("request_id", ""),
            "intent_type": state.get("intent_type"),
            "final_result": state.get("final_result", ""),
            "generated_files": state.get("generated_files", []),
        }

    def _error_response(
        self,
        state: AgentState,
        error_msg: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "success": False,
            "request_id": state.get("request_id", ""),
            "error": error_msg or state.get("error", "Unknown error"),
            "final_result": state.get("final_result", ""),
            "generated_files": state.get("generated_files", []),
        }


# Global orchestrator instance
orchestrator = AgentOrchestrator()
