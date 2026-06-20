"""
Logging utility with color-coded debug output.
Supports both console (color) and file logging simultaneously.
"""

import sys
import os
from datetime import datetime
from typing import Optional

# ANSI color codes
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Semantic colors
    AGENT_TO_LLM = BLUE       # Agent sending to LLM
    LLM_TO_AGENT = GREEN      # LLM response to Agent
    AGENT_ACTION = CYAN       # Agent executing action
    LLM_DECISION = MAGENTA    # LLM making decision
    ORCHESTRATOR = YELLOW     # Orchestrator state
    ERROR = RED               # Errors
    WARNING = YELLOW          # Warnings
    INFO = WHITE              # General info
    TOOL_CALL = CYAN          # Tool calling
    STATE = GRAY              # State transitions

_LOG_FILE: Optional[str] = None
_LOG_TO_CONSOLE: bool = True


def init_logger(log_file: str = "debugout.log", console: bool = True):
    """Initialize logger with file output."""
    global _LOG_FILE, _LOG_TO_CONSOLE
    _LOG_FILE = os.path.abspath(log_file)
    _LOG_TO_CONSOLE = console
    # Ensure log directory exists
    log_dir = os.path.dirname(_LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    # Clear old log
    with open(_LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"[{_now()}] Log initialized\n")
    log_info(f"Logger initialized -> {_LOG_FILE}")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _write(msg: str, color: str = Colors.WHITE, tag: str = "INFO"):
    """Write log message to console (with color) and file."""
    timestamp = _now()
    colored = f"{color}{msg}{Colors.RESET}" if color and _LOG_TO_CONSOLE else msg
    plain = f"[{timestamp}] [{tag}] {msg}"
    
    # Remove color codes for file output
    clean = plain
    
    if _LOG_TO_CONSOLE:
        print(colored, file=sys.stderr)
    
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(clean + "\n")
        except Exception:
            pass  # Silently fail if can't write to log file


def log_agent_to_llm(msg: str):
    """Agent sending message to LLM (BLUE)."""
    _write(f"[Agent→LLM] {msg}", Colors.AGENT_TO_LLM, "A→LLM")


def log_llm_to_agent(msg: str):
    """LLM response to Agent (GREEN)."""
    _write(f"[LLM→Agent] {msg}", Colors.LLM_TO_AGENT, "LLM→A")


def log_agent_action(msg: str):
    """Agent executing an action (CYAN)."""
    _write(f"[Action] {msg}", Colors.AGENT_ACTION, "ACTION")


def log_llm_decision(msg: str):
    """LLM making a decision (MAGENTA)."""
    _write(f"[LLM-Decision] {msg}", Colors.LLM_DECISION, "DECISION")


def log_orchestrator(msg: str):
    """Orchestrator state change (YELLOW)."""
    _write(f"[Orchestrator] {msg}", Colors.ORCHESTRATOR, "ORCH")


def log_error(msg: str):
    """Error message (RED)."""
    _write(f"[ERROR] {msg}", Colors.ERROR, "ERROR")


def log_warning(msg: str):
    """Warning message (YELLOW)."""
    _write(f"[WARN] {msg}", Colors.WARNING, "WARN")


def log_info(msg: str):
    """General info message (WHITE)."""
    _write(f"[INFO] {msg}", Colors.INFO, "INFO")


def log_tool_call(msg: str):
    """Tool call message (CYAN)."""
    _write(f"[Tool] {msg}", Colors.TOOL_CALL, "TOOL")


def log_state(msg: str):
    """State transition message (GRAY)."""
    _write(f"[State] {msg}", Colors.STATE, "STATE")


def log_section(title: str):
    """Print a section divider."""
    divider = "=" * 60
    _write(f"\n{divider}", Colors.BOLD, "SECTION")
    _write(f"  {title}", Colors.BOLD, "SECTION")
    _write(f"{divider}\n", Colors.BOLD, "SECTION")
