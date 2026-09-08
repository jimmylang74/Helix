"""
EventBase — 外部世界事件基类与事件子类。

感知层（事件源 Producer：定时器线程、IM 轮询线程、未来的 Webhook/MCP/邮件）
把外部世界的一次"感知"封装为事件对象，投递到 EventBus（modules/events/bus.py），
由 EventBroker（modules/events/broker.py）按 event_type 路由到不同的 Channel 处理器。

解耦约定：生产者不知道也不会决定"这个事件由谁处理"——它只负责感知 + 生成
事件 + publish。路由决策集中在 EventBroker 一处。
"""

import time
import uuid
from typing import Any, Dict, Optional


class EventBase:
    """事件基类：统一字段 + 子类覆写的路由信息。

    - event_type : 路由键（如 "cron.timer" / "wechat.message"），由子类覆写
    - source     : 事件来源（"cron" / "wechat"），分类/观测用
    - payload    : 子类构造时传入的具名字段（无结构约束，够用即止）
    """

    event_type: str = ""
    source: str = ""

    def __init__(self, **payload: Any) -> None:
        self.event_id: str = uuid.uuid4().hex
        self.occurred_at: float = time.time()   # 生产时刻（感知打点）
        self.received_at: float = 0.0           # 进入 EventBus 时刻（bus 打点）
        self.payload: Dict[str, Any] = dict(payload)

    def get(self, key: str, default: Any = None) -> Any:
        """读取 payload 具名字段。"""
        return self.payload.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "occurred_at": self.occurred_at,
            "received_at": self.received_at,
            "payload": self.payload,
        }

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(event_id={self.event_id}, "
            f"event_type={self.event_type}, source={self.source}, "
            f"payload_keys={list(self.payload.keys())})"
        )


class TimerEvent(EventBase):
    """定时任务触发事件 — CronScheduler 调度线程产生并投递。"""

    event_type = "cron.timer"
    source = "cron"

    def __init__(self, task: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(task=task or {})


class WechatEvent(EventBase):
    """微信消息事件 — iLinkBot getupdates 轮询线程封装产生并投递。"""

    event_type = "wechat.message"
    source = "wechat"

    def __init__(
        self,
        sender_id: str = "",
        sender_name: str = "",
        content: str = "",
        context_token: str = "",
        raw: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            context_token=context_token,
            raw=raw,
        )


__all__ = ["EventBase", "TimerEvent", "WechatEvent"]