"""
Context Manager - Manages conversation history and task context.
"""

import json
from typing import Any, Dict, List, Optional
from modules.core.agent_state import AgentState, state_to_context, get_todo_progress
from modules.utils.logger import log_agent_action, log_state


class ContextManager:
    """Manages agent context including conversation history and state tracking."""

    def __init__(self):
        self._conversations: Dict[str, List[Dict[str, str]]] = {}

    def initialize(self, state: AgentState):
        """Initialize context for a new request."""
        request_id = state.get("request_id", "unknown")
        self._conversations[request_id] = []
        log_state(f"Context initialized for request {request_id}")

    def add_message(self, state: AgentState, role: str, content: str):
        """Add a message to the conversation history."""
        request_id = state.get("request_id", "unknown")
        if request_id not in self._conversations:
            self._conversations[request_id] = []
        self._conversations[request_id].append({
            "role": role,
            "content": content,
            "phase": state.get("orchestrator_phase", "unknown"),
        })
        # Keep history manageable (last 50 messages)
        if len(self._conversations[request_id]) > 50:
            self._conversations[request_id] = self._conversations[request_id][-50:]

    def get_conversation(self, state: AgentState) -> List[Dict[str, str]]:
        """Get conversation history for the current request."""
        request_id = state.get("request_id", "unknown")
        return self._conversations.get(request_id, [])

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

    def build_subtask_context(self, state: AgentState) -> str:
        """Build focused context for the current subtask."""
        parts = [
            f"## User Request\n{state.get('user_request', '')}",
            f"\n## Todo Progress\n{get_todo_progress(state)}",
            f"\n## Current Subtask\n{state.get('current_subtask', '')}",
            f"\n## Subtask Status: {state.get('subtask_status', 'idle')}",
            f"\n## Current Loop Count: {state.get('subtask_loop_count', 0)}",
        ]

        # Last few subtask history entries
        history = state.get("subtask_history", [])
        recent = history[-5:] if history else []
        if recent:
            parts.append("\n## Recent Subtask History")
            for h in recent:
                parts.append(f"- {h.get('subtask', '')}: {h.get('status', '')}")

        # Collected data summary
        data = state.get("collected_data", [])
        if data:
            parts.append(f"\n## Collected Data ({len(data)} items)")
            for d in data[-3:]:
                parts.append(f"\n{d[:1000]}")

        return "\n".join(parts)

    def clear(self, request_id: str):
        """Clear context for a request."""
        if request_id in self._conversations:
            del self._conversations[request_id]
            log_state(f"Context cleared for request {request_id}")


# Global context manager instance
context_manager = ContextManager()
