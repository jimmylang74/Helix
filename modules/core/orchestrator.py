"""
Agent Orchestrator - Core dual-loop architecture.
Loop 1 (Todo Loop): Iterates through todo_list items.
Loop 2 (Subtask Loop): For each todo, handles research/tool execution.
"""

import json
import uuid
from typing import Any, Dict, List, Optional

from modules.core.agent_state import (
    AgentState, create_initial_state, is_subtask_complete,
    all_todos_complete, get_current_todo, get_todo_progress
)
from modules.core.context_manager import context_manager
from modules.core.todo_manager import todo_manager
from modules.agents.tool_base import tool_registry
from modules.llm.llm_client import LLMClient, LLMResponse, ToolDefinition
from modules.prompts.system_prompts import (
    ORCHESTRATOR_SYSTEM_PROMPT, TODO_PLANNING_PROMPT,
    SUBTASK_DECOMPOSE_PROMPT, SUBTASK_DECISION_PROMPT, SUBTASK_SUMMARY_PROMPT,
    TODO_SUMMARY_PROMPT, SUMMARIZATION_PROMPT, AGENT_SYSTEM_PROMPT
)
from modules.prompts.ppt_prompts import PPT_SYSTEM_PROMPT, PPT_TODO_PROMPT, PPT_FULL_DESIGN_PROMPT
from modules.prompts.search_prompts import RESEARCH_SYSTEM_PROMPT, RESEARCH_TODO_PROMPT, CONTENT_ANALYSIS_PROMPT, FINAL_ANSWER_PROMPT
from modules.prompts.coding_prompts import CODING_SYSTEM_PROMPT, CODING_TODO_PROMPT, CODE_ANALYSIS_PROMPT
from modules.utils.logger import (
    log_orchestrator, log_agent_action, log_llm_decision,
    log_error, log_info, log_section, log_agent_to_llm, log_llm_to_agent, log_tool_call
)
from modules.llm.llm_events import set_request_context, clear_request_context, cleanup as llm_cleanup, emit as _emit_llm_event, get_request_context
from modules.core import status_events
from modules.utils.file_ops import FileOps


class AgentOrchestrator:
    """Main orchestrator with dual-loop architecture."""

    def __init__(self):
        self.llm = LLMClient()
        self.file_ops = FileOps()
        self._active_states: Dict[str, AgentState] = {}

    def _get_system_prompt(self, intent_type: str) -> str:
        """Get the appropriate system prompt for the intent type."""
        prompts = {
            "ppt": PPT_SYSTEM_PROMPT,
            "research": RESEARCH_SYSTEM_PROMPT,
            "coding": CODING_SYSTEM_PROMPT,
        }
        return prompts.get(intent_type, ORCHESTRATOR_SYSTEM_PROMPT)

    def _get_todo_prompt(self, intent_type: str) -> str:
        """Get the appropriate todo planning prompt."""
        prompts = {
            "ppt": PPT_TODO_PROMPT,
            "research": RESEARCH_TODO_PROMPT,
            "coding": CODING_TODO_PROMPT,
        }
        return prompts.get(intent_type, TODO_PLANNING_PROMPT)

    def process_request(self, user_request: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Main entry point - process a user request end-to-end."""
        if not request_id:
            request_id = f"req_{uuid.uuid4().hex[:12]}"

        set_request_context(request_id)
        log_section(f"Processing Request: {request_id}")
        log_info(f"User request: {user_request[:200]}")

        # Create initial state
        state = create_initial_state(user_request, request_id)
        self._active_states[request_id] = state
        context_manager.initialize(state)

        try:
            # Step 1: Intent Routing & Todo Planning
            self._planning_phase(state)
            if state.get("error"):
                return self._error_response(state)

            # Step 2: Determine loop level (simple vs complex)
            self._determine_loop_level(state)

            # Step 3: Todo Loop (Loop 1)
            self._todo_loop(state)

            # Step 4: Summarization
            self._summarization_phase(state)

            # Final result
            result = {
                "success": True,
                "request_id": request_id,
                "intent_type": state.get("intent_type"),
                "final_result": state.get("final_result", ""),
                "generated_files": state.get("generated_files", []),
                "todos_completed": len(state.get("todos_completed", [])),
                "subtask_loops": sum(
                    h.get("loop_count", 0) for h in state.get("subtask_history", [])
                ),
            }
            log_section(f"Request completed: {request_id}")
            return result

        except Exception as e:
            log_error(f"Orchestrator error: {e}")
            import traceback
            log_error(traceback.format_exc())
            state["error"] = str(e)
            state["orchestrator_phase"] = "done"
            status_events.emit(request_id, state)
            return self._error_response(state, str(e))
        finally:
            llm_cleanup(request_id)
            clear_request_context()

    def _error_response(self, state: AgentState, error_msg: Optional[str] = None) -> Dict[str, Any]:
        """Build error response."""
        return {
            "success": False,
            "request_id": state.get("request_id", ""),
            "error": error_msg or state.get("error", "Unknown error"),
            "final_result": state.get("final_result", ""),
            "generated_files": state.get("generated_files", []),
        }

    def _planning_phase(self, state: AgentState):
        """Phase 1: Intent routing and todo planning."""
        state["orchestrator_phase"] = "planning"
        log_section("Phase 1: Planning")

        # Get LLM to determine intent and create todos
        context = context_manager.build_llm_context(state, include_history=False)
        system_prompt = ORCHESTRATOR_SYSTEM_PROMPT

        log_agent_to_llm("Sending request to LLM for intent routing and planning...")

        response = self.llm.decide_json(
            prompt=context + "\n\n" + TODO_PLANNING_PROMPT.format(user_request=state["user_request"]),
            system_prompt=system_prompt
        )

        # Extract intent and todos
        intent_type = response.get("intent_type", "research")
        if intent_type not in ("ppt", "research", "coding"):
            intent_type = "research"

        todos = response.get("todos", [])
        if not todos:
            todos = [f"Process: {state['user_request'][:100]}"]

        state["intent_type"] = intent_type
        log_llm_decision(f"Intent: {intent_type}, Todos: {len(todos)}")

        todo_manager.set_todos(state, todos)
        state["todo_subtask_lists"] = [[] for _ in todos]
        context_manager.add_message(state, "assistant", json.dumps(response, ensure_ascii=False))
        status_events.emit(state["request_id"], state)

    def _determine_loop_level(self, state: AgentState):
        """Determine if this is a simple (1-loop) or complex (2-loop) request."""
        todo_count = len(state.get("todo_list", []))
        complexity = len(state["user_request"])

        # Simple = research-only or coding with just 1-2 clear todos
        # Complex = PPT generation or research with 3+ todos
        if state["intent_type"] == "ppt":
            state["loop_level"] = "complex"
        elif todo_count <= 2:
            state["loop_level"] = "simple"
        else:
            state["loop_level"] = "complex"

        log_orchestrator(f"Loop level: {state['loop_level']} ({todo_count} todos)")

    def _todo_loop(self, state: AgentState):
        """Loop 1: Iterate through todo items."""
        state["orchestrator_phase"] = "todo_loop"
        log_section("Phase 2: Todo Loop (Loop 1)")

        loop_count = 0
        while not todo_manager.is_finished(state):
            if self.is_cancelled(state):
                log_orchestrator("Todo loop cancelled by user")
                break

            loop_count += 1
            if loop_count > state.get("max_todo_loops", 50):
                state["error"] = "Max todo loops exceeded"
                log_error("Max todo loops exceeded")
                break

            current_todo = todo_manager.get_current_todo(state)
            if not current_todo:
                break

            log_orchestrator(f"\n{'='*50}")
            log_orchestrator(f"Todo [{state['current_todo_idx'] + 1}/{len(state['todo_list'])}]: {current_todo}")

            previous_todo_summary = context_manager.get_previous_todo_summary(state)
            if previous_todo_summary:
                log_orchestrator(f"  Previous todo summary available ({len(previous_todo_summary)} chars)")

            result = self._subtask_loop(state, current_todo, previous_todo_summary)

            todo_summary = self._generate_todo_summary(state, current_todo, result)
            context_manager.save_todo_summary(state, current_todo, todo_summary)

            all_done = todo_manager.advance_todo(state, todo_summary)
            log_orchestrator(f"Todo completed. Progress:\n{todo_manager.get_progress(state)}")
            status_events.emit(state["request_id"], state)

        log_orchestrator("Todo Loop completed.")

    def _subtask_loop(self, state: AgentState, todo_item: str, previous_todo_summary: str = "") -> str:
        """Execute a todo by decomposing it into subtasks, then running each independently."""
        state["orchestrator_phase"] = "subtask_loop"
        state["current_subtask"] = todo_item
        state["subtask_status"] = "running"
        status_events.emit(state["request_id"], state)

        system_prompt = self._get_system_prompt(state["intent_type"])
        tool_definitions = self._build_tool_definitions()

        context_manager.reset_subtask_contexts(state)
        context_manager.reset_conversation(state)

        subtasks = self._decompose_todo(state, todo_item, previous_todo_summary, system_prompt)
        log_orchestrator(f"  Decomposed into {len(subtasks)} subtask(s)")

        todo_idx = state.get("current_todo_idx", 0)
        todo_subtask_lists = state.get("todo_subtask_lists", [])
        if todo_idx < len(todo_subtask_lists):
            todo_subtask_lists[todo_idx] = [
                {"subtask": s, "status": "pending"} for s in subtasks
            ]

        for idx, subtask in enumerate(subtasks, 1):
            state["subtask_loop_count"] = idx
            state["current_subtask_idx"] = idx - 1
            if todo_idx < len(todo_subtask_lists):
                todo_subtask_lists[todo_idx][idx - 1]["status"] = "running"
            log_orchestrator(f"  Subtask [{idx}/{len(subtasks)}]: {subtask}")

            previous_subtask_summary = context_manager.get_previous_subtask_summary(state)

            result = self._execute_single_subtask(
                state, subtask, idx, len(subtasks),
                previous_subtask_summary, tool_definitions, system_prompt
            )

            subtask_status = "completed" if result else "failed"
            if todo_idx < len(todo_subtask_lists):
                todo_subtask_lists[todo_idx][idx - 1]["status"] = subtask_status
            status_events.emit(state["request_id"], state)

            subtask_summary = self._generate_subtask_summary(state, subtask, result, tool_definitions, system_prompt)
            context_manager.save_subtask_summary(state, subtask, subtask_summary)

        all_summaries = context_manager.get_all_subtask_summaries(state)
        state["subtask_status"] = "completed"

        history = state.get("subtask_history", [])
        history.append({
            "subtask": todo_item,
            "status": "completed",
            "loop_count": len(subtasks),
            "tool_calls": [],
            "result": all_summaries,
        })
        state["subtask_history"] = history

        return all_summaries or todo_item

    def _decompose_todo(self, state: AgentState, todo_item: str, previous_todo_summary: str, system_prompt: str) -> List[str]:
        """Ask LLM to break a todo into concrete subtasks."""
        context_parts = []
        if previous_todo_summary:
            context_parts.append(f"## Previous Todo Result\n{previous_todo_summary}\n")

        prompt = SUBTASK_DECOMPOSE_PROMPT.format(
            user_request=state.get("user_request", ""),
            todo_item=todo_item,
        )
        if context_parts:
            prompt = "\n\n".join(context_parts) + "\n\n" + prompt

        log_agent_to_llm("Decomposing todo into subtasks...")
        response = self.llm.decide_json(prompt=prompt, system_prompt=system_prompt)

        subtasks = response.get("subtasks", [])
        if not subtasks:
            subtasks = [todo_item]

        context_manager.add_message(state, "assistant", json.dumps(response, ensure_ascii=False))
        return subtasks

    def _execute_single_subtask(
        self,
        state: AgentState,
        subtask: str,
        subtask_index: int,
        subtask_count: int,
        previous_subtask_summary: str,
        tool_definitions: List[ToolDefinition],
        system_prompt: str,
    ) -> str:
        """Execute one subtask with tool-calling support."""
        subtask_result = ""
        max_iterations = 5

        context_manager.reset_conversation(state)

        for iteration in range(1, max_iterations + 1):
            if self.is_cancelled(state):
                log_orchestrator("Subtask loop cancelled by user")
                break

            collected = state.get("collected_data", [])
            collected_summary = "\n".join(d[:500] for d in collected[-3:]) if collected else "(none)"

            context_parts = []
            if previous_subtask_summary:
                context_parts.append(f"## Previous Subtask Result\n{previous_subtask_summary}\n")

            prompt = SUBTASK_DECISION_PROMPT.format(
                user_request=state.get("user_request", ""),
                subtask_index=subtask_index,
                subtask_count=subtask_count,
                subtask=subtask,
                collected_data=collected_summary,
            )
            if context_parts:
                prompt = "\n\n".join(context_parts) + "\n\n" + prompt

            history = context_manager.get_conversation(state)

            log_agent_to_llm(f"Subtask {subtask_index} iter {iteration}: decision...")
            llm_response = self.llm.with_tools(
                prompt=prompt,
                tools=tool_definitions,
                system_prompt=system_prompt,
                context_messages=history[-10:] if history else None,
            )

            response_data = self._parse_llm_response(llm_response.content)
            context_manager.add_message(state, "assistant", llm_response.content)

            tool_calls = response_data.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    self._execute_tool_call(state, tc)
                continue

            subtask_result = response_data.get("response") or ""
            log_llm_decision(f"Subtask {subtask_index} result: {subtask_result[:200]}")
            break

        return subtask_result

    def _generate_subtask_summary(
        self,
        state: AgentState,
        subtask: str,
        result: str,
        tool_definitions: List[ToolDefinition],
        system_prompt: str,
    ) -> str:
        """Generate a concise summary of a completed subtask."""
        if not result:
            return ""

        prompt = SUBTASK_SUMMARY_PROMPT.format(
            subtask=subtask,
            work_performed=result[:2000],
        )

        log_agent_to_llm(f"Generating summary for subtask: {subtask[:50]}...")
        response = self.llm.decide_json(prompt=prompt, system_prompt=system_prompt)

        summary = response.get("summary", result[:500])
        log_llm_decision(f"Subtask summary: {summary[:150]}...")
        return summary

    def _generate_todo_summary(self, state: AgentState, todo: str, subtask_results: str) -> str:
        """Generate a summary of a completed todo from its subtask results."""
        if not subtask_results:
            return ""

        prompt = TODO_SUMMARY_PROMPT.format(
            todo=todo,
            subtask_results=subtask_results[:3000],
        )

        system_prompt = self._get_system_prompt(state["intent_type"])
        log_agent_to_llm(f"Generating summary for todo: {todo[:50]}...")
        response = self.llm.decide_json(prompt=prompt, system_prompt=system_prompt)

        summary = response.get("summary", subtask_results[:500])
        log_llm_decision(f"Todo summary: {summary[:150]}...")
        return summary

    def _build_tool_definitions(self) -> List[ToolDefinition]:
        """Collect all available tool definitions from ToolRegistry."""
        tool_definitions = []

        for tool in tool_registry.get_enabled_tools():
            tool_definitions.append(ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            ))

        return tool_definitions

    def _parse_llm_response(self, content: str) -> Dict[str, Any]:
        """Parse LLM JSON response."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to find JSON in the content
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    pass
            return {"response": content}

    def _execute_tool_call(self, state: AgentState, tool_call: Dict[str, Any]) -> str:
        """Execute a tool call from LLM decision. Returns the result string."""
        name = tool_call.get("name", "")
        arguments = tool_call.get("arguments", {})
        tc_id = tool_call.get("id", "")
        log_tool_call(f"Executing tool: {name}({json.dumps(arguments, ensure_ascii=False)})")

        result_text = ""
        try:
            if name == "web_search":
                query = arguments.get("query", "")
                result_text = tool_registry.call_tool("web_search", {"query": query})
                results = json.loads(result_text) if result_text else []

                urls = [r["url"] for r in results if r.get("url")]
                state["urls_to_fetch"] = urls

                self._emit_tool_result(tc_id, name, f"搜索到 {len(results)} 条结果，获取 {len(urls)} 个网页...")

                formatted = json.dumps(results, ensure_ascii=False)
                context_manager.add_message(state, "assistant",
                    f"[web_search results for '{query}']\n{formatted}")

                if urls:
                    log_agent_action(f"Auto-fetching {len(urls)} URLs...")
                    fetched = tool_registry.call_tool("web_fetch_batch", {"urls": urls})
                    state["fetched_content"].append(fetched)
                    state["collected_data"].append(fetched)
                    context_manager.add_message(state, "assistant",
                        f"[Auto-fetched {len(urls)} URLs from web_search results]\n{fetched[:2000]}")
                    result_text = fetched

            elif name == "image_search":
                query = arguments.get("query", "")
                max_results = arguments.get("max_results", 5)
                result_text = tool_registry.call_tool("image_search", {"query": query, "max_results": max_results})
                results = json.loads(result_text) if result_text else []

                urls = [r["url"] for r in results if r.get("url")]
                state["urls_to_fetch"].extend(urls)

                self._emit_tool_result(tc_id, name, f"搜索到 {len(results)} 张图片")

                if state.get("intent_type") == "ppt" and urls:
                    log_agent_action(f"Auto-downloading {len(urls)} images...")
                    saved = tool_registry.call_tool("image_download", {"urls": urls})
                    state["generated_files"].extend(saved)

                context_manager.add_message(state, "assistant",
                    f"[image_search results for '{query}']\n{json.dumps(results, ensure_ascii=False)}")

            else:
                try:
                    result_text = tool_registry.call_tool(name, arguments)
                    preview = result_text[:500] + "..." if len(result_text) > 500 else result_text
                    self._emit_tool_result(tc_id, name, preview)
                    context_manager.add_message(state, "assistant",
                        f"[{name} results]\n{json.dumps(result_text, ensure_ascii=False, default=str)}")
                except Exception:
                    log_error(f"Unknown tool: {name}")
                    self._emit_tool_result(tc_id, name, f"错误: 未知工具 {name}")
                    context_manager.add_message(state, "assistant",
                        f"[tool error] Unknown tool: {name}")
                    result_text = f"Unknown tool: {name}"

        except Exception as e:
            log_error(f"Tool execution failed: {name}: {e}")
            self._emit_tool_result(tc_id, name, f"错误: {e}")
            context_manager.add_message(state, "assistant",
                f"[tool error] {name}: {e}")
            result_text = f"Error: {e}"

        return result_text

    def _emit_tool_result(self, tc_id: str, name: str, result_preview: str):
        """Emit a tool_call_result event to the SSE stream for the frontend."""
        request_id = get_request_context()
        if request_id:
            _emit_llm_event(request_id, {
                "type": "tool_call_result",
                "id": tc_id,
                "name": name,
                "result": result_preview,
            })

    def _summarization_phase(self, state: AgentState):
        """Phase 3: Summarize all results."""
        state["orchestrator_phase"] = "summarizing"
        log_section("Phase 3: Summarization")

        # Build summarization context
        todo_results = todo_manager.get_completed_summary(state)
        generated = state.get("generated_files", [])

        prompt = SUMMARIZATION_PROMPT.format(
            user_request=state["user_request"],
            todo_results=todo_results,
            generated_files="\n".join(generated) if generated else "None"
        )

        log_agent_to_llm("Requesting final summary from LLM...")

        response = self.llm.decide_json(
            prompt=prompt,
            system_prompt=self._get_system_prompt(state["intent_type"])
        )

        summary = response.get("summary", response.get("response", ""))
        state["final_result"] = summary
        state["orchestrator_phase"] = "done"

        log_llm_decision(f"Summary generated ({len(summary)} chars)")
        status_events.emit(state["request_id"], state)

    def cancel_request(self, request_id: str) -> bool:
        """Cancel an active request. Returns True if found and cancelled."""
        state = self._active_states.get(request_id)
        if not state:
            return False
        state["cancelled"] = True
        state["orchestrator_phase"] = "done"
        state["error"] = "Cancelled by user"
        log_orchestrator(f"Request {request_id} cancelled by user")
        status_events.emit(request_id, state)
        return True

    def is_cancelled(self, state: AgentState) -> bool:
        """Check if a request has been cancelled."""
        return state.get("cancelled", False)

    def get_state(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get current state for a request."""
        state = self._active_states.get(request_id)
        if not state:
            return None
        return {
            "request_id": request_id,
            "intent_type": state.get("intent_type"),
            "orchestrator_phase": state.get("orchestrator_phase"),
            "todo_progress": get_todo_progress(state),
            "current_todo": get_current_todo(state),
            "todo_list": state.get("todo_list", []),
            "current_todo_idx": state.get("current_todo_idx", 0),
            "todos_completed": state.get("todos_completed", []),
            "todo_subtask_lists": state.get("todo_subtask_lists", []),
            "current_subtask_idx": state.get("current_subtask_idx", 0),
            "subtask_status": state.get("subtask_status"),
            "subtask_history": state.get("subtask_history", []),
            "final_result": state.get("final_result", ""),
            "generated_files": state.get("generated_files", []),
            "error": state.get("error"),
            "cancelled": state.get("cancelled", False),
        }

    def refresh_llm(self):
        """Refresh LLM client (call after config change)."""
        self.llm.refresh()


# Global orchestrator instance
orchestrator = AgentOrchestrator()
