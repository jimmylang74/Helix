"""
外部世界事件输入基础设施（Event Base / EventBus / EventBroker）。

感知层事件源（定时器线程 / IM 轮询线程 / 未来 Webhook/MCP/邮件）把外部世界的
感知封装为 EventBase 子类，投递到 EventBus（进程内发布/订阅总线），由
EventBroker 按 event_type 路由到不同的 Channel 处理器 —— 消息类型与 Channel
类型解耦：新增消息源不再需要新增 Channel。
"""

from modules.events.base import EventBase, TimerEvent, WechatEvent
from modules.events.broker import EventBroker, get_event_broker
from modules.events.bus import EventBus, get_event_bus

__all__ = [
    "EventBase",
    "TimerEvent",
    "WechatEvent",
    "EventBus",
    "get_event_bus",
    "EventBroker",
    "get_event_broker",
]