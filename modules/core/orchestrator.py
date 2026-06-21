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
from modules.mcp.mcp_registry import registry as mcp_registry
from modules.llm.llm_client import LLMClient, LLMResponse, ToolDefinition
from modules.prompts.system_prompts import (
    ORCHESTRATOR_SYSTEM_PROMPT, TODO_PLANNING_PROMPT,
    SUBTASK_DECISION_PROMPT, SUMMARIZATION_PROMPT, AGENT_SYSTEM_PROMPT
)
from modules.prompts.ppt_prompts import PPT_SYSTEM_PROMPT, PPT_TODO_PROMPT, PPT_FULL_DESIGN_PROMPT
from modules.prompts.search_prompts import RESEARCH_SYSTEM_PROMPT, RESEARCH_TODO_PROMPT, CONTENT_ANALYSIS_PROMPT, FINAL_ANSWER_PROMPT
from modules.prompts.coding_prompts import CODING_SYSTEM_PROMPT, CODING_TODO_PROMPT, CODE_ANALYSIS_PROMPT
from modules.utils.logger import (
    log_orchestrator, log_agent_action, log_llm_decision,
    log_error, log_info, log_section, log_agent_to_llm, log_llm_to_agent, log_tool_call
)
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
            return self._error_response(state, str(e))

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

        # Initialize todo list
        todo_manager.set_todos(state, todos)
        context_manager.add_message(state, "assistant", json.dumps(response, ensure_ascii=False))

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

            # Execute subtask loop for this todo
            result = self._subtask_loop(state, current_todo)

            # Mark todo as completed
            all_done = todo_manager.advance_todo(state, result)
            log_orchestrator(f"Todo completed. Progress:\n{todo_manager.get_progress(state)}")

        log_orchestrator("Todo Loop completed.")

    def _subtask_loop(self, state: AgentState, todo_item: str) -> str:
        """Loop 2: Execute a single todo's subtask loop with LLM-driven decisions."""
        state["orchestrator_phase"] = "subtask_loop"
        state["current_subtask"] = todo_item
        state["subtask_status"] = "running"
        state["subtask_loop_count"] = 0

        subtask_history_entry = {
            "subtask": todo_item,
            "status": "running",
            "loop_count": 0,
            "tool_calls": [],
        }

        loop_count = 0
        max_loops = state.get("max_subtask_loops", 20)
        subtask_complete = False
        subtask_result = ""
        system_prompt = self._get_system_prompt(state["intent_type"])

        # Initialize MCP registry if not yet done
        if not self._mcp_initialized:
            try:
                mcp_registry.initialize()
                self._mcp_initialized = True
            except Exception as e:
                log_error(f"MCP initialization failed: {e}")

        tool_definitions = []
        intent = state.get("intent_type", "research")

        for tool in tool_registry.get_enabled_tools():
            tool_definitions.append(ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            ))

        mcp_tools = mcp_registry.get_tools_for_intent(intent)
        for mt in mcp_tools:
            existing_names = {t.name for t in tool_definitions}
            if mt.name not in existing_names:
                tool_definitions.append(ToolDefinition(
                    name=mt.name,
                    description=mt.description,
                    parameters=mt.input_schema,
                ))
                log_info(f"MCP tool added for intent '{intent}': {mt.name}")

        while not subtask_complete and loop_count < max_loops:
            loop_count += 1
            state["subtask_loop_count"] = loop_count
            log_orchestrator(f"  Subtask Loop iteration {loop_count}/{max_loops}")

            # Build context for LLM decision
            context = context_manager.build_subtask_context(state)

            # Get LLM decision
            log_agent_to_llm(f"Subtask loop {loop_count}: Asking LLM for decision...")
            llm_response = self.llm.with_tools(
                prompt=context + "\n\n" + f"## Decision Required\nSubtask: {todo_item}\nWhat should I do next?",
                tools=tool_definitions,
                system_prompt=system_prompt
            )

            # Parse response
            response_data = self._parse_llm_response(llm_response.content)

            # Process tool calls if any
            tool_calls = response_data.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    self._execute_tool_call(state, tc)
                    subtask_history_entry["tool_calls"].append(tc)
            else:
                # No tool calls - LLM is providing direct response
                subtask_result = response_data.get("response", "")
                log_llm_decision(f"LLM direct response: {subtask_result[:200]}")

            # Check completion flags
            if response_data.get("subtask_complete") or response_data.get("all_complete"):
                log_orchestrator(f"✅ Subtask completed by LLM decision")
                subtask_complete = True

            # Add to history
            context_manager.add_message(state, "assistant", llm_response.content)

        # Update subtask history
        subtask_history_entry["status"] = "completed" if subtask_complete else "max_loops"
        subtask_history_entry["loop_count"] = loop_count
        subtask_history_entry["result"] = subtask_result

        history = state.get("subtask_history", [])
        history.append(subtask_history_entry)
        state["subtask_history"] = history
        state["subtask_status"] = "completed" if subtask_complete else "failed"

        return subtask_result or todo_item

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

    def _execute_tool_call(self, state: AgentState, tool_call: Dict[str, Any]):
        """Execute a tool call from LLM decision."""
        name = tool_call.get("name", "")
        arguments = tool_call.get("arguments", {})
        log_tool_call(f"Executing tool: {name}({json.dumps(arguments, ensure_ascii=False)})")

        try:
            if name == "web_search":
                query = arguments.get("query", "")
                try:
                    result_text = mcp_registry.call_tool("web_search", {"query": query})
                    results = json.loads(result_text) if result_text else []
                except Exception:
                    results = tool_registry.call_tool("web_search", {"query": query})

                urls = [r["url"] for r in results if r.get("url")]
                state["urls_to_fetch"] = urls

                formatted = json.dumps(results, ensure_ascii=False)
                context_manager.add_message(state, "assistant",
                    f"[web_search results for '{query}']\n{formatted}")

                if urls:
                    log_agent_action(f"Auto-fetching {len(urls)} URLs...")
                    fetched = tool_registry.call_tool("web_fetch_batch", {"urls": urls})
                    state["fetched_content"].append(fetched)
                    state["collected_data"].append(fetched)
                    context_manager.add_message(state, "assistant",
                        f"[web_fetch_batch completed: {len(urls)} URLs]")

            elif name == "image_search":
                query = arguments.get("query", "")
                max_results = arguments.get("max_results", 5)
                try:
                    result_text = mcp_registry.call_tool("image_search", {"query": query, "max_results": max_results})
                    results = json.loads(result_text) if result_text else []
                except Exception:
                    results = tool_registry.call_tool("image_search", {"query": query, "max_results": max_results})

                urls = [r["url"] for r in results if r.get("url")]
                state["urls_to_fetch"].extend(urls)

                if state.get("intent_type") == "ppt" and urls:
                    log_agent_action(f"Auto-downloading {len(urls)} images...")
                    saved = tool_registry.call_tool("image_download", {"urls": urls})
                    state["generated_files"].extend(saved)

                context_manager.add_message(state, "assistant",
                    f"[image_search results for '{query}']\n{json.dumps(results, ensure_ascii=False)}")

            else:
                try:
                    result_text = mcp_registry.call_tool(name, arguments)
                    context_manager.add_message(state, "assistant",
                        f"[{name} results]\n{result_text}")
                except Exception:
                    try:
                        result = tool_registry.call_tool(name, arguments)
                        context_manager.add_message(state, "assistant",
                            f"[{name} results]\n{json.dumps(result, ensure_ascii=False, default=str)}")
                    except Exception:
                        log_error(f"Unknown tool: {name}")
                        context_manager.add_message(state, "assistant",
                            f"[tool error] Unknown tool: {name}")

        except Exception as e:
            log_error(f"Tool execution failed: {name}: {e}")
            context_manager.add_message(state, "assistant",
                f"[tool error] {name}: {e}")

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
            "subtask_status": state.get("subtask_status"),
            "generated_files": state.get("generated_files"),
            "error": state.get("error"),
        }

    def refresh_llm(self):
        """Refresh LLM client (call after config change)."""
        self.llm.refresh()


# Global orchestrator instance
orchestrator = AgentOrchestrator()
