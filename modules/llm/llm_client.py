"""
LLM Client — Unified interface via ai_engine submodule.

All LLM calls go through ai_engine.run_engine() using --output-format events.
Events are captured from stdout and parsed into structured responses.
"""

import io
import json
import os
import re
import sys
from argparse import Namespace
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from modules.config.config_manager import ConfigManager
from modules.llm.llm_events import get_request_context, emit as _emit_event
from modules.utils.logger import (
    log_agent_to_llm, log_llm_to_agent, log_error, log_info, log_llm_decision,
)

# ── ai_engine submodule path ────────────────────────────────────────
_AI_ENGINE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "ai_engine",
)
if _AI_ENGINE_DIR not in sys.path:
    sys.path.insert(0, _AI_ENGINE_DIR)

from ai_engine import run_engine as _run_engine, _init_verbose, _close_verbose


# ═══════════════════════════════════════════════════════════════════════
# Stdout stream wrapper — forwards NDJSON lines to the event bus
# ═══════════════════════════════════════════════════════════════════════

class _StdoutEventEmitter:
    """Tee stdout writes to the in-memory event bus.

    Wraps the underlying ``StringIO`` buffer.  Each ``write()`` call
    forwards complete JSON lines to ``llm_events.emit()`` while still
    accumulating data for the caller's buffer.

    When ``enabled=False``, LLM stream output is captured but NOT forwarded
    to the frontend (used for silent parallel node execution — Option B).
    """

    def __init__(self, buf: io.StringIO, enabled: bool = True):
        self._buf = buf
        self._partial = ""
        self._enabled = enabled

    def write(self, s: str) -> int:
        self._buf.write(s)
        if self._enabled:
            request_id = get_request_context()
            if request_id:
                self._partial += s
                while "\n" in self._partial:
                    line, self._partial = self._partial.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        _emit_event(request_id, event)
                    except json.JSONDecodeError:
                        pass
        return len(s)

    def flush(self):
        self._buf.flush()

    def getvalue(self) -> str:
        return self._buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════
# Data classes (unchanged interface for orchestrator compatibility)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ToolDefinition:
    """Tool definition for LLM function calling."""
    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass
class LLMResponse:
    """Structured LLM response."""
    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    # ai_engine "usage" event dict (prompt_tokens/completion_tokens/reasoning_tokens); None if unreported.
    usage: Optional[Dict[str, Any]] = None


# ═══════════════════════════════════════════════════════════════════════
# LLM Client
# ═══════════════════════════════════════════════════════════════════════


class LLMClient:
    """Unified LLM client — delegates to ai_engine submodule."""

    def __init__(self):
        self.config = ConfigManager()
        self._provider = self.config.get("llm.provider", "ollama_native")
        self._log_file = self._resolve_log_path()
        log_info(f"LLM client initialized: provider={self._provider} (via ai_engine)")

    # ── Public API (orchestrator-compatible) ───────────────────────

    def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[ToolDefinition]] = None,
        system_prompt: Optional[str] = None,
        expect_json: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Send chat to LLM and get structured response via ai_engine events."""
        # Build a single text from messages
        text = self._messages_to_text(messages)
        return self._call_engine(
            text=text,
            system_prompt=system_prompt,
            no_stream=False,
            expect_json=expect_json,
            temperature=temperature,
            top_p=top_p,
        )

    def simple_chat(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """One-shot chat — returns plain text."""
        response = self.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            expect_json=False,
        )
        return response.content

    def ask_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[ToolDefinition]] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send prompt and expect a JSON document.

        If ``tools`` is given, the tool catalog is injected into the system
        prompt as context so the LLM can reference real tool names — but the
        response contract stays a JSON document (no tool-calling channel).
        """
        if tools:
            system_prompt = f"{system_prompt or ''}\n\n{self.format_tools_section(tools)}"
        response = self.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            expect_json=True,
            temperature=temperature,
            top_p=top_p,
        )
        return self._extract_json(response.content)

    def ask_with_tools(
        self,
        prompt: str,
        tools: List[ToolDefinition],
        system_prompt: Optional[str] = None,
        context_messages: Optional[List[Dict[str, str]]] = None,
        emit_stream: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Chat with tool-calling support via JSON-based protocol.

        Tool descriptions are injected into the system prompt so the LLM
        can respond with ``{"tool_calls": [...]}`` when appropriate.

        Args:
            emit_stream: Whether to emit LLM streaming events to frontend.
                         Set to False for silent parallel node execution.
            temperature/top_p: Optional sampling overrides. When None the
                         global llm config defaults are used.
        """
        # Build combined system prompt with tool descriptions
        combined_system = (
            f"{system_prompt or ''}\n\n"
            f"{self.format_tools_section(tools, heading='Available tools (respond with JSON that includes tool_calls if needed):')}"
        )

        # Flatten context messages + prompt into a single text
        parts: List[str] = []
        if context_messages:
            for m in context_messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                if content:
                    parts.append(f"[{role}]: {content}")
        parts.append(prompt)
        flat_text = "\n\n".join(parts)

        return self._call_engine(
            text=flat_text,
            system_prompt=combined_system,
            no_stream=False,
            expect_json=True,
            emit_stream=emit_stream,
            temperature=temperature,
            top_p=top_p,
        )

    @staticmethod
    def format_tools_section(
        tools: List[ToolDefinition],
        heading: str = "## Available Tools",
    ) -> str:
        """Format tool definitions as a prompt block: heading + one entry per tool."""
        parts = [heading]
        for t in tools:
            parts.append(f"- {t.name}: {t.description}")
            parts.append(
                f"  Parameters: {json.dumps(t.parameters, ensure_ascii=False)}"
            )
        return "\n".join(parts)

    def refresh(self):
        """Re-read configuration (call after config change)."""
        log_info("Refreshing LLM client...")
        self.config = ConfigManager()
        self._provider = self.config.get("llm.provider", "ollama_native")
        self._log_file = self._resolve_log_path()
        log_info(f"LLM client refreshed: provider={self._provider}")

    # ── Internal helpers ──────────────────────────────────────────

    def _resolve_log_path(self) -> Optional[str]:
        """Resolve the LLM engine log file path from config."""
        log_file = self.config.get("llm.log_file", "")
        if not log_file:
            return None
        # Relative paths are relative to project root
        if not os.path.isabs(log_file):
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            log_file = os.path.join(project_root, log_file)
        return log_file

    def _build_engine_args(
        self,
        text: str,
        system_prompt: Optional[str] = None,
        no_stream: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Namespace:
        """Build ai_engine Namespace args from current config."""
        llm_config = self.config.get_llm_config()
        verbose = llm_config.get("verbose", True)
        log_path = self._log_file if verbose else None

        return Namespace(
            provider=llm_config.get("provider", "ollama_native"),
            model=llm_config.get("model", "qwen2.5:7b"),
            endpoint=llm_config.get("endpoint", "http://localhost:11434"),
            api_key=llm_config.get("api_key") or None,
            temperature=(
                temperature if temperature is not None
                else llm_config.get("temperature", 0.2)
            ),
            top_p=(
                top_p if top_p is not None
                else llm_config.get("top_p", 0.9)
            ),
            prompt_file=None,
            prompt_text=system_prompt,
            file=None,
            text=text,
            no_stream=no_stream,
            output_format="events",
            get_provider=False,
            verbose=verbose,
            log=log_path,
        )

    def _call_engine(
        self,
        text: str,
        system_prompt: Optional[str] = None,
        no_stream: bool = False,
        expect_json: bool = True,
        emit_stream: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Run ai_engine and parse NDJSON events into an LLMResponse.

        Args:
            emit_stream: If False, LLM streaming events are NOT forwarded
                         to the frontend (silent execution for parallel nodes).
            temperature/top_p: Optional sampling overrides. When None the
                         global llm config defaults are used.
        """
        args = self._build_engine_args(
            text, system_prompt, no_stream, temperature=temperature, top_p=top_p
        )

        log_agent_to_llm(
            f"LLM call via ai_engine: provider={args.provider}, model={args.model}\n"
            f"  system_prompt: {system_prompt or ''}\n"
            f"  user_message: {text}"
        )

        request_id = get_request_context()
        if emit_stream and request_id:
            _emit_event(request_id, {
                "type": "sending",
                "provider": args.provider,
                "model": args.model,
                "temperature": args.temperature,
                "top_p": args.top_p,
                "system_prompt": system_prompt or "",
                "user_message": text,
            })

        # Initialize verbose logging for this call
        _init_verbose(args)

        # Capture stdout — run_engine writes NDJSON events to stdout
        buf = io.StringIO()
        events: List[Dict[str, Any]] = []
        content = ""
        tool_calls: List[Dict[str, Any]] = []
        finish_reason = "stop"
        thinking_content = ""
        usage: Optional[Dict[str, Any]] = None

        try:
            emitter = _StdoutEventEmitter(buf, enabled=emit_stream)
            with redirect_stdout(emitter):
                try:
                    _run_engine(args)
                except SystemExit as exc:
                    if exc.code and exc.code != 0:
                        log_error(f"ai_engine exited with code {exc.code}")
                        finish_reason = "error"
        except Exception as e:
            log_error(f"LLM call failed: {e}")
            raise
        finally:
            _close_verbose()

        # Parse NDJSON events from captured stdout
        output = emitter.getvalue()
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Not a JSON event line (could be litellm warning etc.) — skip
                continue

            events.append(event)
            etype = event.get("type", "")

            if etype == "assistant":
                content = event.get("content", "")
            elif etype == "assistant_delta":
                content += event.get("delta", "")
            elif etype == "thinking":
                thinking_content = event.get("content", "")
            elif etype == "thinking_delta":
                thinking_content += event.get("delta", "")
            elif etype == "tool_call_begin":
                tool_calls.append({
                    "id": event.get("id", ""),
                    "name": event.get("name", ""),
                    "arguments": {},
                })
            elif etype == "tool_call_delta":
                # Accumulate argument JSON string
                for tc in tool_calls:
                    if tc["id"] == event.get("id", ""):
                        tc["_arg_buf"] = tc.get("_arg_buf", "") + event.get("delta", "")
                        break
            elif etype == "tool_call_end":
                for tc in tool_calls:
                    if tc["id"] == event.get("id", ""):
                        # In non-streaming, arguments come as parsed object
                        if "arguments" in event and isinstance(event["arguments"], dict):
                            tc["arguments"] = event["arguments"]
                        elif "_arg_buf" in tc:
                            try:
                                tc["arguments"] = json.loads(tc["_arg_buf"])
                            except (json.JSONDecodeError, TypeError):
                                tc["arguments"] = tc["_arg_buf"]
                            del tc["_arg_buf"]
                        break
            elif etype == "done":
                finish_reason = event.get("finish_reason", "stop")
            elif etype == "usage":
                usage = event

        # Clean up internal keys from tool_calls
        for tc in tool_calls:
            tc.pop("_arg_buf", None)

        # Log LLM response
        log_llm_to_agent(f"Response: {content}")

        # If thinking was present, prepend it for JSON extraction context
        if expect_json and thinking_content and not content:
            content = thinking_content

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    @staticmethod
    def _messages_to_text(messages: List[Dict[str, str]]) -> str:
        """Flatten a messages list into a single text block."""
        parts: List[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if content:
                parts.append(f"[{role}]: {content}")
        return "\n\n".join(parts)

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        """Extract JSON from LLM response text.

        Tolerant to common LLM output defects: markdown code fences,
        surrounding prose, and raw control characters (e.g. literal
        newlines) inside string values.
        """
        text = text.strip()

        # Direct JSON parse
        if text.startswith("{") and text.endswith("}"):
            parsed = LLMClient._try_parse_json(text)
            if parsed is not None:
                return parsed

        # Fenced code block
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            parsed = LLMClient._try_parse_json(json_match.group(1).strip())
            if parsed is not None:
                return parsed

        # Brace-delimited
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            parsed = LLMClient._try_parse_json(brace_match.group(0))
            if parsed is not None:
                return parsed

        # Fallback
        return {"response": text}

    @staticmethod
    def _try_parse_json(candidate: str) -> Optional[Dict[str, Any]]:
        """Parse a JSON candidate: strict first, then json_repair fallback.

        json_repair handles common LLM output defects (raw control
        characters in strings, trailing commas, etc.). Import is guarded so
        the fallback degrades gracefully if the optional dependency is
        missing.
        """
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        try:
            import json_repair
            parsed = json_repair.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return None
