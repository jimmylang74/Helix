"""
Code Tools Plugin - Code saving and execution.
"""

import os
from typing import Any
from datetime import datetime

from modules.agents.tool_base import BaseTool
from modules.utils.logger import log_tool_call, log_agent_action
from modules.utils.file_ops import FileOps


class SaveCodeTool(BaseTool):
    """Save generated code to the output directory."""

    name = "save_code"
    description = "Save generated code to a file in the output directory. Returns the saved file path."
    category = "code"
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The source code to save"
            },
            "filename": {
                "type": "string",
                "description": "Desired filename (e.g., 'fibonacci.py')"
            },
            "language": {
                "type": "string",
                "description": "Programming language (default: py)",
                "default": "py"
            }
        },
        "required": ["code", "filename"]
    }

    def execute(self, code: str = "", filename: str = "", language: str = "py", **kwargs) -> str:
        log_tool_call(f"save_code(filename='{filename}', language='{language}')")
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"
        )
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"code_{timestamp}_{filename}"
        filepath = os.path.join(output_dir, safe_name)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        log_agent_action(f"Code saved: {filepath}")
        return filepath


class RunCodeTool(BaseTool):
    """Run a code file and return output."""

    name = "run_code"
    description = "Execute a Python code file and return stdout/stderr output."
    category = "code"
    parameters = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Path to the code file to run"
            },
            "args": {
                "type": "string",
                "description": "Command-line arguments to pass",
                "default": ""
            }
        },
        "required": ["filepath"]
    }

    def execute(self, filepath: str = "", args: str = "", **kwargs) -> str:
        log_tool_call(f"run_code(filepath='{filepath}', args='{args}')")
        log_agent_action(f"Running code: {filepath} {args}")
        return FileOps.bash(f"python3 {filepath} {args}", timeout=30)
