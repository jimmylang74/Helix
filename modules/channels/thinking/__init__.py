"""
Thinking channel — 独立"思考"通道（外部事件驱动，固定 Thinking 意图）。

架构与 cron 对称：事件源（RSS 轮询 / WebHook 回调）把外部感知封装为
RssEvent / WebhookEvent 投递到 EventBus，由 EventBroker 路由到本通道的
handle_event()，通道以独立线程消费并固定以 Thinking 意图（forced_intent，
独立 LLM 日志 llm_engine_thinking.log）交给私有 agent 运行时处理，结果写入
db/thinking.db，并可按配置推送到输出通道。

- store.py        : 通道配置（db/thinking.json）+ 事件结果（db/thinking.db）+ RSS 去重
- sources.py      : RssEventSource / WebhookEventSource 事件源
- webhook_routes.py: HTTP WebHook 端点（Flask blueprint）
- channel.py      : ThinkingChannel 适配器
"""

from modules.channels.thinking.channel import ThinkingChannel, format_result_message
from modules.channels.thinking.sources import (
    RssEventSource,
    WebhookEventSource,
    get_rss_source,
    get_webhook_source,
)

__all__ = [
    "ThinkingChannel",
    "format_result_message",
    "RssEventSource",
    "WebhookEventSource",
    "get_rss_source",
    "get_webhook_source",
]