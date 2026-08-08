"""
Intent Store — IntentProvider implementation via the IntentRouter.

Wraps modules.agent.intent_router (real intent routing/storage logic) so the
orchestrator depends only on the HelixCore.interface.IntentProvider
protocol. Implements the HelixCore.interface.IntentProvider protocol
(formerly direct intent_router calls).

The ``router`` constructor argument is injectable for testing; it defaults to
the real global intent_router instance.
"""

from typing import Any, Dict, Optional

from HelixCore.interface import IntentProvider


class IntentStore(IntentProvider):
    """Adapter that forwards intent queries/CRUD to the IntentRouter."""

    def __init__(self, router: Any = None):
        if router is None:
            from modules.agent.intent_router import intent_router as default_router

            router = default_router
        self._router = router

    def get_registered_intents(self) -> Dict[str, Any]:
        return self._router.get_registered_intents()

    def get_intent_info(self, intent_type: str) -> Optional[Dict[str, Any]]:
        return self._router.get_intent_info(intent_type)

    def register_intent(self, intent_type: str, name: str, description: str) -> bool:
        return self._router.register_intent(intent_type, name, description)

    def update_intent(self, intent_type: str, data: Dict[str, Any]) -> bool:
        return self._router.update_intent(intent_type, data)

    def delete_intent(self, intent_type: str) -> bool:
        return self._router.delete_intent(intent_type)

    def get_available_intents(self) -> Dict[str, Any]:
        return self._router.get_available_intents()

    def get_enabled_intent_ids(self) -> set:
        return self._router.get_enabled_intent_ids()


# Host 侧全局实例（供 routes 等 host 组件直接使用）
intent_store = IntentStore()
