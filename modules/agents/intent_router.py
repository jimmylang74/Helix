"""
Intent Router - Routes user requests to appropriate agent templates.
Supports PPT generation, Research, and Coding intents.
Pre-registered templates with admin configurable settings.
"""

import json
from typing import Any, Dict, Optional
from modules.config.config_manager import ConfigManager
from modules.prompts.task_graph_prompts import GENERIC_INTENT_ID
from modules.utils.logger import log_agent_action, log_llm_decision, log_error, log_info


class IntentRouter:
    """
    Routes user requests to appropriate agent templates.
    Uses LLM to classify intent, then selects the matching template.
    """

    def __init__(self):
        self.config = ConfigManager()

    def get_registered_intents(self) -> Dict[str, Any]:
        """Get all registered intents from config."""
        return self.config.get("intents", {})

    def get_intent_info(self, intent_type: str) -> Optional[Dict[str, Any]]:
        """Get info about a specific intent."""
        return self.config.get(f"intents.{intent_type}")

    def register_intent(self, intent_type: str, name: str, description: str) -> bool:
        """Register a new intent template."""
        try:
            intents = self.config.get("intents", {})
            intents[intent_type] = {
                "enabled": True,
                "name": name,
                "description": description,
            }
            self.config.update_section("intents", intents)
            log_info(f"Intent registered: {intent_type} ({name})")
            return True
        except Exception as e:
            log_error(f"Failed to register intent: {e}")
            return False

    def update_intent(self, intent_type: str, data: Dict[str, Any]) -> bool:
        """Update an existing intent.

        generic 为固定兜底意图，禁止禁用（enabled 恒为 True）。
        """
        try:
            if intent_type == GENERIC_INTENT_ID:
                data = dict(data)
                data["enabled"] = True
            self.config.set(f"intents.{intent_type}", data)
            log_info(f"Intent updated: {intent_type}")
            return True
        except Exception as e:
            log_error(f"Failed to update intent: {e}")
            return False

    def delete_intent(self, intent_type: str) -> bool:
        """Delete an intent. generic 为固定兜底意图，禁止删除。"""
        try:
            if intent_type == GENERIC_INTENT_ID:
                log_error(f"Refused to delete fixed intent: {intent_type}")
                return False
            intents = self.config.get("intents", {})
            if intent_type in intents:
                del intents[intent_type]
                self.config.update_section("intents", intents)
                log_info(f"Intent deleted: {intent_type}")
                return True
            return False
        except Exception as e:
            log_error(f"Failed to delete intent: {e}")
            return False

    def get_available_intents(self) -> Dict[str, Any]:
        """Get list of enabled intents for routing."""
        intents = self.config.get("intents", {})
        return {
            k: v for k, v in intents.items()
            if v.get("enabled", True)
        }

    def get_enabled_intent_ids(self) -> set:
        """Get set of enabled intent IDs (generic always included)."""
        intents = self.config.get("intents", {})
        ids = {GENERIC_INTENT_ID}
        ids.update(
            i for i, c in intents.items()
            if i != GENERIC_INTENT_ID and c.get("enabled", True)
        )
        return ids



# Global intent router
intent_router = IntentRouter()
