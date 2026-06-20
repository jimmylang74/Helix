"""
LLM Client - Unified interface for multiple LLM providers.
Supports Ollama, OpenAI-compatible, and Gemini.
"""

import json
import re
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field

from modules.config.config_manager import ConfigManager
from modules.utils.logger import log_agent_to_llm, log_llm_to_agent, log_error, log_info, log_llm_decision


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


class LLMClient:
    """Unified LLM client supporting multiple providers."""

    def __init__(self):
        self.config = ConfigManager()
        self._provider = None
        self._client = None
        self._setup_client()

    def _setup_client(self):
        """Initialize the LLM client based on configuration."""
        llm_config = self.config.get_llm_config()
        self._provider = llm_config.get("provider", "ollama")

        if self._provider == "ollama":
            self._setup_ollama(llm_config)
        elif self._provider in ("openai", "deepseek"):
            self._setup_openai_compat(llm_config)
        elif self._provider == "gemini":
            self._setup_gemini(llm_config)
        else:
            raise ValueError(f"Unsupported LLM provider: {self._provider}")

        log_info(f"LLM client initialized: provider={self._provider}")

    def _setup_ollama(self, config: Dict[str, Any]):
        """Setup Ollama client."""
        try:
            from langchain_ollama import ChatOllama
            self._client = ChatOllama(
                base_url=config.get("base_url", "http://localhost:11434"),
                model=config.get("model", "qwen2.5:7b"),
                temperature=config.get("temperature", 0.7),
                num_predict=config.get("max_tokens", 4096),
                format="json",  # Force JSON output
            )
        except ImportError as e:
            log_error(f"Failed to import Ollama client: {e}")
            raise

    def _setup_openai_compat(self, config: Dict[str, Any]):
        """Setup OpenAI-compatible client (OpenAI, DeepSeek, etc)."""
        try:
            from langchain_openai import ChatOpenAI
            self._client = ChatOpenAI(
                api_key=config.get("api_key", ""),
                base_url=config.get("base_url", "https://api.openai.com/v1"),
                model=config.get("model", "gpt-4o"),
                temperature=config.get("temperature", 0.7),
                max_tokens=config.get("max_tokens", 4096),
                model_kwargs={"response_format": {"type": "json_object"}},
            )
        except ImportError as e:
            log_error(f"Failed to import OpenAI client: {e}")
            raise

    def _setup_gemini(self, config: Dict[str, Any]):
        """Setup Gemini client."""
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            self._client = ChatGoogleGenerativeAI(
                api_key=config.get("api_key", ""),
                model=config.get("model", "gemini-2.0-flash"),
                temperature=config.get("temperature", 0.7),
                max_output_tokens=config.get("max_tokens", 4096),
            )
        except ImportError as e:
            log_error(f"Failed to import Gemini client: {e}")
            raise

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from LLM response text."""
        # Try direct JSON parse first
        text = text.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        
        # Try to find JSON block
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass
        
        # Try to find {...} in text
        brace_match = re.search(r'\{.*\}', text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass
        
        # Last resort: return as raw text wrapped in JSON
        return {"response": text}

    def _build_tool_schema(self, tools: List[ToolDefinition]) -> List[Dict[str, Any]]:
        """Build tool schemas for LLM function calling."""
        schemas = []
        for tool in tools:
            schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters
                }
            }
            schemas.append(schema)
        return schemas

    def _convert_to_openai_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert our tool format to OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {})
                }
            }
            for t in tools
        ]

    def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[ToolDefinition]] = None,
        system_prompt: Optional[str] = None,
        expect_json: bool = True
    ) -> LLMResponse:
        """Send chat to LLM and get response."""
        # Prepend system prompt if provided
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        # Log agent→LLM
        log_agent_to_llm(f"Messages: {len(full_messages)} | Tools: {len(tools) if tools else 0}")
        for m in full_messages[-3:]:  # Show last 3 messages for context
            content_preview = m["content"][:200] if m["content"] else "(empty)"
            log_agent_to_llm(f"  [{m['role']}]: {content_preview}")

        try:
            invoke_kwargs = {}
            
            if tools and self._provider in ("openai", "deepseek"):
                invoke_kwargs["tools"] = self._convert_to_openai_tools(tools)
            
            if self._provider == "ollama":
                invoke_kwargs["format"] = "json" if expect_json else None
            
            result = self._client.invoke(full_messages, **invoke_kwargs)
            
            # Parse response
            content = result.content if hasattr(result, "content") else str(result)
            
            # Log LLM→Agent
            log_llm_to_agent(f"Response received: {content[:300]}...")
            
            # Check for tool calls (OpenAI format)
            tool_calls = []
            if hasattr(result, "additional_kwargs") and "tool_calls" in result.additional_kwargs:
                for tc in result.additional_kwargs["tool_calls"]:
                    tc_info = {
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": json.loads(tc.get("function", {}).get("arguments", "{}"))
                    }
                    tool_calls.append(tc_info)
                    log_llm_decision(f"Tool call requested: {tc_info['name']}")

            finish_reason = "stop"
            if hasattr(result, "response_metadata"):
                finish_reason = result.response_metadata.get("finish_reason", "stop")

            # If it's a JSON mode model, try to extract JSON
            if expect_json:
                try:
                    parsed = self._extract_json(content)
                    content = json.dumps(parsed, ensure_ascii=False)
                except Exception:
                    pass

            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason
            )

        except Exception as e:
            log_error(f"LLM call failed: {e}")
            raise

    def simple_chat(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Simple one-shot chat."""
        response = self.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            expect_json=False
        )
        return response.content

    def decide_json(self, prompt: str, system_prompt: Optional[str] = None) -> Dict[str, Any]:
        """Send prompt and expect JSON response."""
        response = self.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            expect_json=True
        )
        return self._extract_json(response.content)

    def with_tools(
        self,
        prompt: str,
        tools: List[ToolDefinition],
        system_prompt: Optional[str] = None,
        context_messages: Optional[List[Dict[str, str]]] = None
    ) -> LLMResponse:
        """Chat with tool calling support using JSON-based protocol."""
        messages = list(context_messages or [])
        messages.append({"role": "user", "content": prompt})

        # For Ollama and other providers without native tool calling,
        # we inject tool descriptions into the system prompt
        if tools and self._provider == "ollama":
            tool_descriptions = "\n\nAvailable tools (respond with JSON that includes tool_calls if needed):\n"
            for t in tools:
                tool_descriptions += f"\n- {t.name}: {t.description}"
                tool_descriptions += f"\n  Parameters: {json.dumps(t.parameters, ensure_ascii=False)}"
            
            combined_system = f"{system_prompt or ''}\n{tool_descriptions}"
            combined_system += """
            
You MUST respond in JSON format. If you need to use a tool, include a "tool_calls" field:
{
  "thinking": "your reasoning",
  "tool_calls": [{"name": "tool_name", "arguments": {...}}]
}
If you want to respond directly without tools:
{
  "thinking": "your reasoning",
  "response": "your answer here"
}
"""
            return self.chat(messages, system_prompt=combined_system, expect_json=True)
        
        # For providers with native tool calling
        return self.chat(messages, tools=tools, system_prompt=system_prompt, expect_json=True)

    def refresh(self):
        """Re-initialize the client (call after config change)."""
        log_info("Refreshing LLM client...")
        self._setup_client()
