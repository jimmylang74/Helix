"""
LangGraph State definitions for the hybrid AI Agent system.
Defines the state machine types for dual-loop architecture.
"""

from typing import Any, Dict, List, Optional, TypedDict


class SubtaskResult(TypedDict, total=False):
    """Result of a single subtask execution."""
    subtask: str
    status: str  # "completed", "failed", "skipped"
    result: str
    collected_data: Dict[str, Dict[str, Any]]
    generated_files: List[str]
    tool_calls: List[Dict[str, Any]]


class AgentState(TypedDict, total=False):
    """Main Agent State - used by LangGraph."""
    # User facing
    user_request: str
    intent_type: str  # "ppt", "research", "coding"
    request_id: str

    # Todo management
    todo_list: List[str]
    current_todo_idx: int
    todos_completed: List[Dict[str, Any]]

    # Per-todo subtask tracking (parallel to todo_list)
    todo_subtask_lists: List[List[Dict[str, Any]]]
    current_subtask_idx: int  # index of running subtask within current todo's subtask list

    # Subtask execution
    current_subtask: str
    subtask_context: str
    subtask_status: str  # "idle", "running", "needs_research", "needs_tool", "completed", "failed"
    subtask_history: List[Dict[str, Any]]
    subtask_loop_count: int

    # Data collection
    collected_data: Dict[str, Dict[str, Any]]  # {todo_idx: {"title": str, "results": [str]}}
    urls_to_fetch: List[str]
    fetched_content: List[str]

    # File management
    generated_files: List[str]

    # Results
    final_result: str
    error: Optional[str]

    # Conversation
    conversation_history: List[Dict[str, str]]

    # Orchestrator state
    orchestrator_phase: str  # "planning", "todo_loop", "subtask_loop", "summarizing", "done"
    loop_level: str  # "simple" or "complex"
    max_subtask_loops: int
    max_todo_loops: int


def create_initial_state(user_request: str, request_id: str) -> AgentState:
    """Create initial agent state."""
    return {
        "user_request": user_request,
        "intent_type": "",
        "request_id": request_id,

        "todo_list": [],
        "current_todo_idx": -1,
        "todos_completed": [],

        "todo_subtask_lists": [],
        "current_subtask_idx": 0,

        "current_subtask": "",
        "subtask_context": "",
        "subtask_status": "idle",
        "subtask_history": [],
        "subtask_loop_count": 0,

        "collected_data": {},
        "urls_to_fetch": [],
        "fetched_content": [],

        "generated_files": [],

        "final_result": "",
        "error": None,

        "conversation_history": [],

        "orchestrator_phase": "planning",
        "loop_level": "complex",
        "max_subtask_loops": 20,
        "max_todo_loops": 50,
    }


def is_subtask_complete(state: AgentState) -> bool:
    """Check if the current subtask is complete."""
    return state.get("subtask_status") == "completed"


def all_todos_complete(state: AgentState) -> bool:
    """Check if all todos are done."""
    return state.get("current_todo_idx") >= len(state.get("todo_list", []))


def get_current_todo(state: AgentState) -> str:
    """Get the current todo item."""
    idx = state.get("current_todo_idx", 0)
    todos = state.get("todo_list", [])
    if 0 <= idx < len(todos):
        return todos[idx]
    return ""


def get_todo_progress(state: AgentState) -> str:
    """Format todo progress for LLM context."""
    todos = state.get("todo_list", [])
    completed = state.get("todos_completed", [])
    current = state.get("current_todo_idx", 0)

    lines = []
    for i, todo in enumerate(todos):
        prefix = "✅" if i < current else ("🔄" if i == current else "⬜")
        lines.append(f"{prefix} [{i + 1}/{len(todos)}] {todo}")

    return "\n".join(lines)


def _format_collected_data(data: Dict[str, Dict[str, Any]]) -> str:
    """Format collected_data dict for display."""
    if not data:
        return "(none)"
    parts = []
    for todo_idx in sorted(data.keys(), key=lambda x: int(x) if x.isdigit() else 0):
        entry = data[todo_idx]
        title = entry.get("title", "")
        results = entry.get("results", [])
        header = f"### Todo {todo_idx}: {title}" if title else f"### Todo {todo_idx}"
        parts.append(header + "\n" + "\n".join(results))
    result = "\n\n".join(parts)
    return result[-2000:] if len(result) > 2000 else result


def state_to_context(state: AgentState) -> str:
    """Serialize state for LLM context window."""
    parts = [
        f"## User Request\n{state.get('user_request', '')}",
        f"\n## Intent Type: {state.get('intent_type', 'unknown')}",
        f"\n## Orchestrator Phase: {state.get('orchestrator_phase', '')}",
        f"\n## Loop Level: {state.get('loop_level', 'complex')}",
        f"\n## Todo Progress\n{get_todo_progress(state)}",
        f"\n## Current Subtask\n{state.get('current_subtask', '')}",
        f"\n## Subtask Status: {state.get('subtask_status', 'idle')}",
        f"\n## Collected Data\n{_format_collected_data(state.get('collected_data', {}))}",
        f"\n## Generated Files\n{chr(10).join(state.get('generated_files', [])) if state.get('generated_files') else '(none)'}",
        f"\n## Subtask Loop Count: {state.get('subtask_loop_count', 0)}",
    ]
    return "\n".join(parts)
