"""
意图路由端口 — HelixCore 与 Host 之间的 IntentProvider 契约。

IntentProvider 是运行时注入的 Protocol：Host 侧（如 modules/host/intent_store.py
的 IntentStore 适配器）实现它。HelixCore 只依赖该抽象，不关心意图数据是
从配置文件读取、数据库查询还是远程服务获取。

方法面与旧 IntentRouter 保持一致（查询 / 注册 / 更新 / 删除 / 可用集），
generic 为固定内置兜底意图，恒存在且禁止注册/更新/删除。
"""

from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class IntentProvider(Protocol):
    """意图提供者接口 — 由 Host 侧实现并注入 AgentOrchestrator。

    提供已注册意图模板的查询与增删改能力；generic 为固定内置意图，
    恒排最前 / 恒可用 / 禁止修改。
    """

    def get_registered_intents(self) -> Dict[str, Any]:
        """返回全部已注册意图（generic 固定内置，恒排最前）。"""
        ...

    def get_intent_info(self, intent_type: str) -> Optional[Dict[str, Any]]:
        """返回单个意图信息；不存在或非法时返回 None。"""
        ...

    def register_intent(self, intent_type: str, name: str, description: str) -> bool:
        """注册新意图模板；generic 固定内置，禁止注册。"""
        ...

    def update_intent(self, intent_type: str, data: Dict[str, Any]) -> bool:
        """合并更新意图配置（仅覆盖传入字段）；generic 禁止更新。"""
        ...

    def delete_intent(self, intent_type: str) -> bool:
        """删除意图；generic 固定兜底，禁止删除。"""
        ...

    def get_available_intents(self) -> Dict[str, Any]:
        """返回启用中的意图（generic 恒可用）。"""
        ...

    def get_enabled_intent_ids(self) -> set:
        """返回启用意图 ID 集合（generic 恒包含）。"""
        ...
