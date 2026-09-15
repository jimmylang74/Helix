"""
外部世界事件输入基础设施（Event Source / EventBus / EventBroker）。

感知层事件源（定时器线程 / IM 轮询线程 / 未来 Webhook/MCP/邮件）把外部世界的
感知封装为 EventBase 子类，投递到 EventBus（进程内发布/订阅总线），由
EventBroker 按 event_type 路由到不同的 Channel 处理器 —— 消息类型与 Channel
类型解耦：新增消息源不再需要新增 Channel。

事件源侧统一契约：
- EventSource：抽象基类（工作线程生命周期 + publish 计数 + get_status）
- EventSourceRegistry：进程内注册表（组合根统一 start_all/stop_all 启停）
"""

from modules.events.base import EventBase, TimerEvent, WechatEvent
from modules.events.broker import EventBroker, get_event_broker
from modules.events.bus import EventBus, get_event_bus
from modules.events.event_source import EventSource
from modules.events.source_registry import EventSourceRegistry, get_source_registry

__all__ = [
    "EventBase",
    "TimerEvent",
    "WechatEvent",
    "EventSource",
    "EventSourceRegistry",
    "get_source_registry",
    "EventBus",
    "get_event_bus",
    "EventBroker",
    "get_event_broker",
]