"""
Intent Router - Routes user requests to appropriate agent templates.
Supports PPT generation, Research, and Coding intents.
Pre-registered templates with admin configurable settings.
"""

import json
from typing import Any, Dict, Optional
from modules.config.config_manager import ConfigManager
from modules.prompts.task_graph_prompts import (
    GENERIC_INTENT_ID,
    GENERIC_INTENT_NAME,
    GENERIC_INTENT_DESC,
)
from modules.utils.logger import log_agent_action, log_llm_decision, log_error, log_info


class IntentRouter:
    """
    Routes user requests to appropriate agent templates.
    Uses LLM to classify intent, then selects the matching template.
    """

    def __init__(self):
        self.config = ConfigManager()

    def get_registered_intents(self) -> Dict[str, Any]:
        """Get all registered intents from config.

        generic 为固定内置意图，不写入配置文件：始终由代码合成并固定排在
        最前；其余意图保持配置中的注册顺序。返回新 dict，不暴露配置内部引用。
        """
        intents = self.config.get("intents", {})
        if not isinstance(intents, dict):
            intents = {}
        ordered: Dict[str, Any] = {
            GENERIC_INTENT_ID: self._generic_entry(),
        }
        for intent_id, cfg in intents.items():
            if intent_id != GENERIC_INTENT_ID:
                ordered[intent_id] = cfg
        return ordered

    @staticmethod
    def _generic_entry() -> Dict[str, Any]:
        """返回 generic 固定内置意图条目（名称/描述来自常量，enabled 恒为 True）。"""
        return {
            "enabled": True,
            "name": GENERIC_INTENT_NAME,
            "description": GENERIC_INTENT_DESC,
        }

    def get_intent_info(self, intent_type: str) -> Optional[Dict[str, Any]]:
        """Get info about a specific intent. generic 为内置固定意图，不入配置。"""
        if intent_type == GENERIC_INTENT_ID:
            return self._generic_entry()
        return self.config.get(f"intents.{intent_type}")

    def register_intent(self, intent_type: str, name: str, description: str) -> bool:
        """Register a new intent template. generic 为固定内置意图，禁止注册。"""
        try:
            if intent_type == GENERIC_INTENT_ID:
                log_error(f"Refused to register fixed intent: {intent_type}")
                return False
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

        generic 为固定内置意图，禁止更新（不入配置，名称/描述恒为内置常量）。
        合并更新：仅覆盖传入字段（排除路由参数 intent_type），未传字段
        （如 finalizer_prompt、planning_prompt 等）保持不变。
        """
        try:
            if intent_type == GENERIC_INTENT_ID:
                log_error(f"Refused to update fixed intent: {intent_type}")
                return False
            merged = dict(self.config.get(f"intents.{intent_type}") or {})
            for key, value in data.items():
                if key == "intent_type":
                    continue
                merged[key] = value
            self.config.set(f"intents.{intent_type}", merged)
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
        """Get list of enabled intents for routing.

        generic 为固定内置意图，恒可用（不入配置）；其余意图取配置中启用项。
        """
        intents = self.config.get("intents", {})
        if not isinstance(intents, dict):
            intents = {}
        available: Dict[str, Any] = {GENERIC_INTENT_ID: self._generic_entry()}
        for intent_id, cfg in intents.items():
            if intent_id != GENERIC_INTENT_ID and cfg.get("enabled", True):
                available[intent_id] = cfg
        return available

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
