"""
File operations utility for Agent capabilities.
Provides ls, grep, read, write, create, delete, and bash operations.
"""

import os
import json
import shutil
import subprocess
from typing import List, Optional
from datetime import datetime

from modules.utils.logger import log_agent_action, log_error


class FileOps:
    """Agent file operations capabilities."""

    @staticmethod
    def ls(path: str = ".") -> str:
        """List directory contents."""
        try:
            items = os.listdir(path)
            result = []
            for item in sorted(items):
                full = os.path.join(path, item)
                if os.path.isdir(full):
                    result.append(f"📁 {item}/")
                else:
                    size = os.path.getsize(full)
                    result.append(f"📄 {item} ({size} bytes)")
            return "\n".join(result) if result else "(empty directory)"
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def grep(pattern: str, path: str, recursive: bool = True) -> str:
        """Search for pattern in files."""
        try:
            if recursive:
                result = subprocess.run(
                    ["grep", "-rn", pattern, path],
                    capture_output=True, text=True, timeout=10
                )
            else:
                result = subprocess.run(
                    ["grep", "-n", pattern, path],
                    capture_output=True, text=True, timeout=10
                )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr] {result.stderr[:500]}"
            return output[:5000] if output else "No matches found."
        except subprocess.TimeoutExpired:
            return "Grep timed out."
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def read_file(file_path: str) -> str:
        """Read file content."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "rb") as f:
                    content = f.read()
                return f"[Binary file, {len(content)} bytes]"
            except Exception as e:
                return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def write_file(file_path: str, content: str) -> str:
        """Write content to file. Creates parent directories if needed."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            log_agent_action(f"Wrote file: {file_path}")
            return f"File written: {file_path} ({len(content)} bytes)"
        except Exception as e:
            log_error(f"Failed to write file {file_path}: {e}")
            return f"Error writing file: {e}"

    @staticmethod
    def create_file(file_path: str) -> str:
        """Create an empty file."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            if not os.path.exists(file_path):
                open(file_path, "a").close()
            log_agent_action(f"Created file: {file_path}")
            return f"File created: {file_path}"
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def del_file(file_path: str) -> str:
        """Delete file or directory."""
        try:
            if os.path.isdir(file_path):
                shutil.rmtree(file_path)
                return f"Directory deleted: {file_path}"
            elif os.path.exists(file_path):
                os.remove(file_path)
                return f"File deleted: {file_path}"
            else:
                return f"Not found: {file_path}"
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def bash(command: str, timeout: int = 30) -> str:
        """Execute a bash command."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr] {result.stderr[:2000]}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"
            return output.strip()[:10000] or "(no output)"
        except subprocess.TimeoutExpired:
            return "Command timed out."
        except Exception as e:
            return f"Error: {e}"
