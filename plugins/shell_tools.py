"""
Shell Tools Plugin - Shell command execution and file operations.
"""

from typing import Any

from modules.agents.tool_base import BaseTool
from modules.utils.logger import log_tool_call, log_agent_action
from modules.utils.file_ops import FileOps


class BashTool(BaseTool):
    """Execute a shell command."""

    name = "bash"
    description = "Execute a shell (bash) command and return stdout/stderr. Use with caution."
    category = "shell"
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute"
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 30)",
                "default": 30
            }
        },
        "required": ["command"]
    }

    def execute(self, command: str = "", timeout: int = 30, **kwargs) -> str:
        log_tool_call(f"bash(command='{command[:100]}')")
        log_agent_action(f"Executing shell command: {command[:200]}")
        return FileOps.bash(command, timeout=timeout)


class ListFilesTool(BaseTool):
    """List directory contents."""

    name = "ls"
    description = "List files and directories at the given path."
    category = "shell"
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to list (default: current directory)",
                "default": "."
            }
        },
        "required": []
    }

    def execute(self, path: str = ".", **kwargs) -> str:
        log_tool_call(f"ls(path='{path}')")
        return FileOps.ls(path)


class GrepTool(BaseTool):
    """Search for a pattern in files."""

    name = "grep"
    description = "Search for a text pattern in files. Returns matching lines with file paths."
    category = "shell"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The text pattern to search for"
            },
            "path": {
                "type": "string",
                "description": "Directory or file path to search in",
                "default": "."
            },
            "recursive": {
                "type": "boolean",
                "description": "Search recursively (default: true)",
                "default": True
            }
        },
        "required": ["pattern"]
    }

    def execute(self, pattern: str = "", path: str = ".", recursive: bool = True, **kwargs) -> str:
        log_tool_call(f"grep(pattern='{pattern}', path='{path}')")
        return FileOps.grep(pattern, path, recursive)


class ReadFileTool(BaseTool):
    """Read file content."""

    name = "read_file"
    description = "Read and return the contents of a file."
    category = "shell"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to read"
            }
        },
        "required": ["file_path"]
    }

    def execute(self, file_path: str = "", **kwargs) -> str:
        log_tool_call(f"read_file(path='{file_path}')")
        return FileOps.read_file(file_path)


class WriteFileTool(BaseTool):
    """Write content to a file."""

    name = "write_file"
    description = "Write text content to a file. Creates parent directories if needed."
    category = "shell"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to write"
            },
            "content": {
                "type": "string",
                "description": "Text content to write"
            }
        },
        "required": ["file_path", "content"]
    }

    def execute(self, file_path: str = "", content: str = "", **kwargs) -> str:
        log_tool_call(f"write_file(path='{file_path}', {len(content)} bytes)")
        return FileOps.write_file(file_path, content)


class DeleteFileTool(BaseTool):
    """Delete a file or directory."""

    name = "delete_file"
    description = "Delete a file or directory."
    category = "shell"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file or directory to delete"
            }
        },
        "required": ["file_path"]
    }

    def execute(self, file_path: str = "", **kwargs) -> str:
        log_tool_call(f"delete_file(path='{file_path}')")
        return FileOps.del_file(file_path)
