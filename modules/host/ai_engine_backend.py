"""
AI Engine Backend — LLMBackend implementation via the ai_engine submodule.

All LLM calls go through ai_engine.run_engine() using --output-format events.
Each call injects a per-call sink into args.sink; the engine writes its NDJSON
events there and the events are parsed into structured responses.
Implements the HelixCore.interface.LLMBackend protocol (formerly LLMClient).
"""

import io
import json
import os
import re
import sys
from argparse import Namespace
from typing import Any, Dict, List, Optional

from HelixCore.interface import LLMBackend, LLMResponse
from modules.config.config_manager import ConfigManager
from modules.llm.llm_events import get_request_context, emit as _emit_event
from modules.utils.logger import (
    log_agent_to_llm, log_llm_to_agent, log_error, log_info, log_llm_decision,
)
from modules.utils.paths import project_path

# ── ai_engine submodule path ────────────────────────────────────────
_AI_ENGINE_DIR = project_path("ai_engine")
if _AI_ENGINE_DIR not in sys.path:
    sys.path.insert(0, _AI_ENGINE_DIR)

from ai_engine import run_engine as _run_engine, _init_verbose, _close_verbose


# ═══════════════════════════════════════════════════════════════════════
# Stdout stream wrapper — forwards NDJSON lines to the event bus
# ═══════════════════════════════════════════════════════════════════════

class _StdoutEventEmitter:
    """Tee engine output writes to the in-memory event bus.

    Passed as args.sink to run_engine(). Each ``write()`` call forwards
    complete JSON lines to ``llm_events.emit()`` while still accumulating
    data for the caller's buffer.

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
# LLMResponse 与 LLMBackend 协议均定义于 HelixCore.interface（见顶部 import）。
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# AI Engine Backend
# ═══════════════════════════════════════════════════════════════════════


class AIEngineBackend(LLMBackend):
    """AI Engine 后端 — 通过 ai_engine 子模块实现 LLMBackend 协议。

    原 LLMClient；重命名以体现其 Host 侧 LLMBackend 适配器角色。
    """

    def __init__(self, log_file: Optional[str] = None):
        """log_file 为通道级覆盖项；None 时回退 Helix.json 的 llm.log_file。"""
        self.config = ConfigManager()
        self._provider = self.config.get("llm.provider", "ollama_native")
        self._explicit_log_file = log_file
        self._log_file = log_file or self._resolve_log_path()
        log_info(f"LLM client initialized: provider={self._provider} (via ai_engine)")

    @property
    def log_file(self) -> Optional[str]:
        return self._log_file

    # ── Public API (orchestrator-compatible) ───────────────────────

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        expect_json: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Send a prompt to LLM and get structured response via ai_engine events."""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self._call_engine(
            messages=messages,
            expect_json=expect_json,
            temperature=temperature,
            top_p=top_p,
        )

    def simple_chat(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """One-shot chat — returns plain text."""
        response = self.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            expect_json=False,
        )
        return response.content

    def ask_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send prompt and expect a JSON document.

        The system prompt must be fully assembled by the caller (including
        any tool catalog); this client only transports messages to the engine.
        """
        response = self.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            expect_json=True,
            temperature=temperature,
            top_p=top_p,
        )
        return self._extract_json(response.content)

    def ask_with_tools(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context_messages: Optional[List[Dict[str, str]]] = None,
        emit_stream: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Chat with tool-calling support via JSON-based protocol.

        The system prompt must already contain the tool catalog (injected by
        the prompts layer); the LLM responds with ``{"tool_calls": [...]}``
        when appropriate.

        Args:
            emit_stream: Whether to emit LLM streaming events to frontend.
                         Set to False for silent parallel node execution.
            temperature/top_p: Optional sampling overrides. When None the
                         global llm config defaults are used.
        """
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if context_messages:
            messages.extend(m for m in context_messages if m.get("content"))
        messages.append({"role": "user", "content": prompt})

        return self._call_engine(
            messages=messages,
            expect_json=True,
            emit_stream=emit_stream,
            temperature=temperature,
            top_p=top_p,
        )

    def refresh(self):
        """Re-read configuration (call after config change)."""
        log_info("Refreshing LLM client...")
        self.config = ConfigManager()
        self._provider = self.config.get("llm.provider", "ollama_native")
        self._log_file = self._resolve_log_path()
        log_info(f"LLM client refreshed: provider={self._provider}")

    def get_provider_model(self) -> tuple[str, str]:
        """Return ``(provider, model)`` for token estimation (LLMBackend 协议)."""
        llm_config = self.config.get_llm_config()
        return (
            llm_config.get("provider", "ollama_native"),
            llm_config.get("model", ""),
        )

    # ── Internal helpers ──────────────────────────────────────────

    def _resolve_log_path(self) -> Optional[str]:
        """Resolve the LLM engine log file path from config."""
        log_file = self.config.get("llm.log_file", "")
        if not log_file:
            return None
        # Relative paths are relative to project root
        if not os.path.isabs(log_file):
            log_file = project_path(log_file)
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
            system_prompt_file=None,
            system_prompt=system_prompt,
            user_prompt_file=None,
            user_prompt=text,
            no_stream=no_stream,
            output_format="events",
            get_provider=False,
            verbose=verbose,
            log=log_path,
            sink=None,
        )

    def _call_engine(
        self,
        messages: List[Dict[str, str]],
        no_stream: Optional[bool] = None,
        expect_json: bool = True,
        emit_stream: bool = True,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> LLMResponse:
        """Run ai_engine and parse NDJSON events into an LLMResponse.

        Args:
            messages: Role-tagged messages ({"role": "system"|"user"|..., "content": ...}).
                      Each message is prefixed with "[<role>]:\n" before being sent;
                      system-role parts go to the engine's system prompt channel,
                      everything else becomes the user prompt.
            no_stream: If None (default), resolved from config ``llm.stream``
                       (Web 控制台 LLM 配置可切换). Pass an explicit bool to
                       override the configured default.
            emit_stream: If False, LLM streaming events are NOT forwarded
                         to the frontend (silent execution for parallel nodes).
            temperature/top_p: Optional sampling overrides. When None the
                         global llm config defaults are used.
        """
        if no_stream is None:
            no_stream = not self.config.get("llm.stream", True)

        system_parts: List[str] = []
        user_parts: List[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if not content:
                continue
            prefixed = f"[{role}]:\n{content}"
            if role == "system":
                system_parts.append(prefixed)
            else:
                user_parts.append(prefixed)
        system_prompt = "\n\n".join(system_parts) or None
        text = "\n\n".join(user_parts)

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

        buf = io.StringIO()
        events: List[Dict[str, Any]] = []
        content = ""
        tool_calls: List[Dict[str, Any]] = []
        finish_reason = "stop"
        thinking_content = ""
        usage: Optional[Dict[str, Any]] = None

        try:
            emitter = _StdoutEventEmitter(buf, enabled=emit_stream)
            args.sink = emitter
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

        # Parse NDJSON events emitted to the per-call sink
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
    def _extract_json(text: str) -> Dict[str, Any]:
        """Extract JSON from LLM response text.

        Tolerant to common LLM output defects: markdown code fences,
        surrounding prose, and raw control characters (e.g. literal
        newlines) inside string values.
        """
        text = text.strip()

        # Direct JSON parse
        if text.startswith("{") and text.endswith("}"):
            parsed = AIEngineBackend._try_parse_json(text)
            if parsed is not None:
                return parsed

        # Fenced code block
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            parsed = AIEngineBackend._try_parse_json(json_match.group(1).strip())
            if parsed is not None:
                return parsed

        # Brace-delimited
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            parsed = AIEngineBackend._try_parse_json(brace_match.group(0))
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
