"""
Todo Manager - Manages the task todo list and progress tracking.
"""

from typing import Any, Dict, List, Optional
from modules.core.agent_state import AgentState, get_todo_progress
from modules.utils.logger import log_agent_action, log_orchestrator, log_state


class TodoManager:
    """Manages todo list creation, tracking, and completion."""

    def __init__(self):
        pass

    def set_todos(self, state: AgentState, todos: List[str]):
        """Initialize the todo list."""
        state["todo_list"] = todos
        state["current_todo_idx"] = 0
        state["todos_completed"] = []
        log_orchestrator(f"Todo list initialized with {len(todos)} items:")
        for i, t in enumerate(todos):
            log_orchestrator(f"  {i+1}. {t}")

    def get_current_todo(self, state: AgentState) -> Optional[str]:
        """Get the current todo item."""
        idx = state.get("current_todo_idx", 0)
        todos = state.get("todo_list", [])
        if 0 <= idx < len(todos):
            return todos[idx]
        return None

    def advance_todo(self, state: AgentState, result: str) -> bool:
        """Mark current todo as completed and advance to next."""
        current = self.get_current_todo(state)
        if current:
            state["todos_completed"].append({
                "todo": current,
                "result": result,
                "status": "completed"
            })
            log_orchestrator(f"✅ Todo completed: {current}")

        state["current_todo_idx"] += 1

        if state["current_todo_idx"] >= len(state.get("todo_list", [])):
            log_orchestrator("🎉 All todos completed!")
            return True  # All done
        else:
            next_todo = self.get_current_todo(state)
            log_orchestrator(f"➡️ Moving to next todo: {next_todo}")
            return False  # More todos remain

    def get_progress(self, state: AgentState) -> str:
        """Get formatted progress string."""
        return get_todo_progress(state)

    def get_completed_summary(self, state: AgentState) -> str:
        """Get summary of completed todos."""
        completed = state.get("todos_completed", [])
        lines = []
        for i, item in enumerate(completed):
            lines.append(f"\n### Todo {i+1}: {item['todo']}")
            lines.append(f"Status: {item['status']}")
            lines.append(f"Result: {item['result'][:500]}")
        return "\n".join(lines)

    def is_finished(self, state: AgentState) -> bool:
        """Check if all todos are done."""
        return state.get("current_todo_idx") >= len(state.get("todo_list", []))


# Global todo manager
todo_manager = TodoManager()
