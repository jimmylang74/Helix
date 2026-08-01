"""
Agent State — minimal state dict for the three-phase DAG orchestrator.
"""

from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    """Main Agent State for the three-phase DAG orchestrator."""
    user_request: str
    intent_type: str  # "ppt", "research", "coding"
    request_id: str
    forced_intent: str

    # Data collection (set during tool execution)
    urls_to_fetch: List[str]
    fetched_content: List[str]
    generated_files: List[str]

    # Results
    final_result: str
    error: Optional[str]

    # Orchestrator phase
    orchestrator_phase: str  # "planning", "node_loop", "finalizing", "done"

    # Cancellation
    cancelled: bool

    # Token usage tracking (per-call current + request-wide totals)
    token_usage: Dict[str, int]  # input/output current + total


def create_initial_state(user_request: str, request_id: str) -> AgentState:
    """Create initial agent state."""
    return {
        "user_request": user_request,
        "intent_type": "",
        "request_id": request_id,

        "urls_to_fetch": [],
        "fetched_content": [],
        "generated_files": [],

        "final_result": "",
        "error": None,

        "orchestrator_phase": "planning",
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "tokenizer": "",
        },
    }
