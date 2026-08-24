"""
CronChannel — Helix 定时任务通道。

区别于 IM 通道：无外部消息接入，仅承载调度器生命周期。装配要点：

- start(): 绑定并启动 CronScheduler 线程（组合根在 Helix 启动后调用，
           满足"Helix 启动即拉起定时任务"）
- stop()/is_running: 透传调度器状态，使 ChannelManager.stop_all 生效
- send():  无推送出口 —— 任务结果统一落 db/cron.db，前端定时任务页查看
- ask_user/get_context/clear_context: 本通道装配时不注册三件套工具
  （build_channel_runtime(include_channel_tools=False)），这些落点
  正常情况下不会被触达；保留兜底返回提示文本。
"""

from typing import Any, Dict, List

from modules.channels.base import ChannelAdapter, ChannelMessage, ChannelStatus
from modules.channels.cron.scheduler import get_scheduler
from modules.utils.logger import log_debug


class CronChannel(ChannelAdapter):
    """定时任务通道 — 调度线程 + 私有 agent 运行时（agent 类任务用）。"""

    CHANNEL_TYPE = "cron"

    # ── Lifecycle ──────────────────────────────────────────────────────

    @property
    def channel_type(self) -> str:
        return self.CHANNEL_TYPE

    @property
    def is_running(self) -> bool:
        """跟随调度器状态（started 即 running）。"""
        return get_scheduler().is_started

    def start(self) -> None:
        """启动调度线程（幂等；重复调用等价于 no-op）。"""
        scheduler = get_scheduler()
        scheduler.bind(self)
        scheduler.start()

    def stop(self) -> None:
        """停止调度线程（幂等）。"""
        get_scheduler().stop()

    def restore_session(self) -> bool:
        """无持久会话；实际启动由组合根显式调用 start() 完成。"""
        return True

    # ── Messaging ──────────────────────────────────────────────────────

    def send(self, content: str, msg_type: str = "text", **kwargs) -> Dict[str, Any]:
        """无独立推送出口 — 任务结果经 store.save_result 落 db/cron.db。"""
        return {"channel": self.CHANNEL_TYPE, "delivered": False}

    def get_messages(self, limit: int = 50) -> List[ChannelMessage]:
        return []

    def get_status(self) -> ChannelStatus:
        scheduler = get_scheduler()
        status = scheduler.get_status()
        return ChannelStatus(
            channel_type=self.CHANNEL_TYPE,
            is_running=status["status"] == "started",
            is_authenticated=True,
            display_name="定时任务",
            error=status.get("error") or None,
            extra={
                "scheduler": status["status"],
                "task_count": status["task_count"],
                "enabled_count": status["enabled_count"],
                "next_run": status["next_run"] or "",
            },
        )

    # ── 通道工具落点（本通道不注册三件套工具，以下仅为兜底）────────────

    def ask_user(self, request_id: str, question: str) -> str:
        log_debug("[cron] ask_user 被调用 — 定时任务通道不支持向用户提问")
        return "错误: 定时任务为一次性自动执行任务，无法向用户提问，请基于已有信息继续"

    def get_context(self) -> str:
        log_debug("[cron] get_context 被调用 — 定时任务通道无会话上下文")
        return "定时任务为一次性任务，没有历史上下文"

    def clear_context(self) -> str:
        log_debug("[cron] clear_context 被调用 — 定时任务通道无会话可清除")
        return "定时任务为一次性任务，无需清除上下文"
