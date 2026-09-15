"""
CronEventSource — Helix 自维护的定时任务事件源（EventSource 子类，**纯事件生产者**）。

- 继承 EventSource 基类：统一 daemon 线程生命周期（start/stop 幂等）、
  publish 计数与 get_status 观测；source_name="cron"（注册表键）。
- start(): 基类通用实现 + _prepare_start_locked() 钩子重算触发表；
            幂等（已 started 再调直接返回）。
- stop():  停止调度线程；幂等。运行中的任务 worker 不受影响（自然跑完）。
- 热重载:  每个 tick 检查 db/cron.json 的 mtime，用户/工具增删改后
           自动重新加载任务表并重算触发时间。
- 补漏策略: 不回补。重启或停摆期间错过的时点直接跳过，只计算下一次
           未来触发。
- 执行:    到期任务封装为 TimerEvent 经 self.publish() 投递到 EventBus，
           由 EventBroker 路由到 CronChannel.handle_event 执行（system→子进程
           / agent→编排器）。本源不持有通道引用、不直接执行任务 ——
           触发与执行解耦，未来接入 Thinking Channel 时无需改动本调度器。
- 兼容:    get_scheduler() 单例函数名与 is_started 别名保留，既有调用方
           （plugins/cron_tools、modules/app/routes、CronChannel）零改动。
"""

import threading
from datetime import datetime
from typing import Any, Dict, Optional

from modules.channels.cron import store
from modules.events import EventSource, TimerEvent
from modules.utils.logger import log_error, log_info

_TICK_SECONDS = 10


class CronEventSource(EventSource):
    """定时任务调度事件源单例（经 get_scheduler() 获取）。"""

    source_name = "cron"
    thread_name = "cron-scheduler"

    def __init__(self) -> None:
        super().__init__()
        self._next_run: Dict[str, datetime] = {}   # cron_id → 下次触发时刻
        self._last_mtime: float = -1.0

    # ── 兼容旧接口 ─────────────────────────────────────────────────────

    @property
    def is_started(self) -> bool:
        """兼容旧接口：等价基类 is_running（线程态即事实）。"""
        return self.is_running

    # ── 生命周期钩子（基类 start/stop 在持锁状态下调用）────────────────

    def _prepare_start_locked(self) -> None:
        """启动线程前重算任务触发表（原 scheduler.start() 的前置逻辑）。"""
        self._reschedule_locked()

    def _after_stop_locked(self) -> None:
        """停止置位后清空触发表（原 scheduler.stop() 的清理逻辑）。"""
        self._next_run = {}

    # ── 状态查询 ───────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """当前状态摘要：基类观测字段 + 任务数与最近一次全局下次触发时间。"""
        status = super().get_status()
        tasks = store.load_tasks()
        with self._lock:
            next_runs = dict(self._next_run)
        next_run = min(next_runs.values()).strftime("%Y-%m-%d %H:%M:%S") if next_runs else None
        status["status"] = "started" if self.is_started else "stopped"
        status["task_count"] = len(tasks)
        status["enabled_count"] = sum(1 for t in tasks if t.get("enabled", True))
        status["next_run"] = next_run
        status["error"] = status.get("last_error") or ""
        return status

    def get_next_run(self, task_id: str) -> Optional[str]:
        """单个任务的下次触发时间（ISO 文本）；未调度返回 None。"""
        with self._lock:
            nxt = self._next_run.get(task_id)
        return nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else None

    def reload(self) -> None:
        """立即从磁盘重载任务表（工具增删改后调用；外部改动由 tick 自动感知）。"""
        with self._lock:
            self._reschedule_locked()

    # ── 调度循环 ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.wait(_TICK_SECONDS):
            try:
                self._tick()
            except Exception as e:  # 单轮异常不终止调度线程
                self._last_error = str(e)
                log_error(f"[CronEventSource] Tick error: {e}")

    def _tick(self) -> None:
        # 1. 外部改动感知（含文件被手工编辑）
        mtime = store.tasks_mtime()
        if mtime != self._last_mtime:
            with self._lock:
                self._reschedule_locked()

        # 2. 收集到期任务
        now = datetime.now()
        fired_ids: list[str] = []
        with self._lock:
            for task_id, nxt in list(self._next_run.items()):
                if now >= nxt:
                    self._next_run.pop(task_id, None)
                    fired_ids.append(task_id)

        # 3. 到期任务 → TimerEvent 投递 EventBus（由 EventBroker 路由到
        #    CronChannel.handle_event 执行），并重算各自的下次触发时间
        for task_id in fired_ids:
            task = store.get_task(task_id)
            if task is not None and task.get("enabled", True):
                log_info(
                    f"[CronEventSource] Firing '{task['title']}' "
                    f"({task['id']}, type={task['task_type']}) → EventBus"
                )
                self.publish(TimerEvent(task=task))
                if task.get("repeat") == "once":
                    store.disable_task(task_id)
                    log_info(f"[CronEventSource] One-shot task {task_id} disabled after fire")
                else:
                    with self._lock:
                        nxt = store.next_occurrence(task, datetime.now())
                        if nxt is not None:
                            self._next_run[task_id] = nxt
            else:
                log_info(f"[CronEventSource] Task {task_id} removed/disabled — skip")

    def _reschedule_locked(self) -> None:
        """重载任务表并为每个启用的任务计算首次触发时间（调用方持锁）。"""
        self._last_mtime = store.tasks_mtime()
        now = datetime.now()
        self._next_run = {}
        for task in store.load_tasks():
            if not task.get("enabled", True):
                continue
            nxt = store.next_occurrence(task, now)
            if nxt is not None:
                self._next_run[task["id"]] = nxt
            elif task.get("repeat") == "once":
                store.disable_task(task["id"])
                log_info(f"[CronEventSource] One-shot task {task['id']} missed — disabled")
        log_info(
            f"[CronEventSource] Rescheduled {len(self._next_run)} task(s) "
            f"(mtime={self._last_mtime:.0f})"
        )


# ── 进程级单例 ─────────────────────────────────────────────────────────────

_scheduler: Optional[CronEventSource] = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> CronEventSource:
    """获取进程级唯一的调度器实例（plugins 与通道共用同一状态）。"""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = CronEventSource()
        return _scheduler