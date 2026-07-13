"""
Context Manager - Manages hierarchical conversation history and task context.

Context hierarchy:
  request_context     # Global: user request, intent, overall progress
  todo_contexts[]     # Each todo's summary (completed todos' results)
  subtask_contexts[]  # Current todo's subtask summaries (completed subtasks' results)
  conversation[]      # Current subtask's iteration history (tool calls, LLM responses)
"""

import json
from typing import Any, Dict, List, Optional
from modules.core.agent_state import AgentState, state_to_context, get_todo_progress
from modules.utils.logger import log_state


class ContextManager:
    """Manages hierarchical agent context with layered storage."""

    def __init__(self):
        self._states: Dict[str, Dict[str, Any]] = {}

    def _get_state(self, request_id: str) -> Dict[str, Any]:
        if request_id not in self._states:
            self._states[request_id] = {
                "todo_contexts": [],
                "subtask_contexts": [],
                "conversation": [],
            }
        return self._states[request_id]

    def initialize(self, state: AgentState):
        """Initialize context for a new request."""
        request_id = state.get("request_id", "unknown")
        self._states[request_id] = {
            "todo_contexts": [],
            "subtask_contexts": [],
            "conversation": [],
        }
        log_state(f"Context initialized for request {request_id}")

    def clear(self, request_id: str):
        """Clear all context for a request."""
        if request_id in self._states:
            del self._states[request_id]
            log_state(f"Context cleared for request {request_id}")

    # ── Conversation (current subtask's iterations) ────────────────

    def add_message(self, state: AgentState, role: str, content: str):
        """Add a message to current subtask's conversation history."""
        request_id = state.get("request_id", "unknown")
        s = self._get_state(request_id)
        s["conversation"].append({
            "role": role,
            "content": content,
            "phase": state.get("orchestrator_phase", "unknown"),
        })
        if len(s["conversation"]) > 50:
            s["conversation"] = s["conversation"][-50:]

    def get_conversation(self, state: AgentState) -> List[Dict[str, str]]:
        """Get current subtask's conversation history."""
        request_id = state.get("request_id", "unknown")
        return self._get_state(request_id).get("conversation", [])

    def reset_conversation(self, state: AgentState):
        """Clear current subtask's conversation (for new subtask)."""
        request_id = state.get("request_id", "unknown")
        self._get_state(request_id)["conversation"] = []

    # ── Subtask Contexts (within current todo) ─────────────────────

    def save_subtask_summary(self, state: AgentState, subtask: str, summary: str):
        """Save a completed subtask's summary."""
        request_id = state.get("request_id", "unknown")
        s = self._get_state(request_id)
        s["subtask_contexts"].append({
            "subtask": subtask,
            "summary": summary,
        })

    def get_previous_subtask_summary(self, state: AgentState) -> str:
        """Get the most recent subtask's summary (for next subtask's input)."""
        request_id = state.get("request_id", "unknown")
        contexts = self._get_state(request_id).get("subtask_contexts", [])
        if contexts:
            return contexts[-1].get("summary", "")
        return ""

    def get_all_subtask_summaries(self, state: AgentState) -> str:
        """Get all completed subtask summaries (for todo summary)."""
        request_id = state.get("request_id", "unknown")
        contexts = self._get_state(request_id).get("subtask_contexts", [])
        if not contexts:
            return "(no subtask results)"
        parts = []
        for i, ctx in enumerate(contexts, 1):
            parts.append(f"### Subtask {i}: {ctx['subtask']}\n{ctx['summary']}")
        return "\n\n".join(parts)

    def reset_subtask_contexts(self, state: AgentState):
        """Clear subtask contexts (for new todo)."""
        request_id = state.get("request_id", "unknown")
        self._get_state(request_id)["subtask_contexts"] = []

    # ── Todo Contexts (across todos) ───────────────────────────────

    def save_todo_summary(self, state: AgentState, todo: str, summary: str):
        """Save a completed todo's summary."""
        request_id = state.get("request_id", "unknown")
        s = self._get_state(request_id)
        s["todo_contexts"].append({
            "todo": todo,
            "summary": summary,
        })

    def get_previous_todo_summary(self, state: AgentState) -> str:
        """Get the most recent completed todo's summary (for next todo's input)."""
        request_id = state.get("request_id", "unknown")
        contexts = self._get_state(request_id).get("todo_contexts", [])
        if contexts:
            return contexts[-1].get("summary", "")
        return ""

    def get_all_todo_summaries(self, state: AgentState) -> str:
        """Get all completed todo summaries (for final summarization)."""
        request_id = state.get("request_id", "unknown")
        contexts = self._get_state(request_id).get("todo_contexts", [])
        if not contexts:
            return "(no todo results)"
        parts = []
        for i, ctx in enumerate(contexts, 1):
            parts.append(f"### Todo {i}: {ctx['todo']}\n{ctx['summary']}")
        return "\n\n".join(parts)

    # ── Legacy compatibility ───────────────────────────────────────

    def build_llm_context(self, state: AgentState, include_history: bool = True) -> str:
        """Build full context string for LLM."""
        parts = [state_to_context(state)]

        if include_history:
            history = self.get_conversation(state)
            if history:
                parts.append("\n## Recent Conversation History")
                for msg in history[-10:]:
                    role_tag = "User" if msg["role"] == "user" else "Assistant"
                    content_preview = msg["content"][:500]
                    parts.append(f"\n### {role_tag}:\n{content_preview}")

        return "\n".join(parts)


# Global context manager
context_manager = ContextManager()
