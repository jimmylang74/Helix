"""
CronScheduler — Helix 自维护的定时任务调度器（区别于系统 crond），**纯事件生产者**。

- start(): 启动独立 daemon 线程，按 tick 周期扫描到期任务并派发执行；
           幂等（已 started 再调直接返回）。
- stop():  停止调度线程；幂等。运行中的任务 worker 不受影响（自然跑完）。
- 热重载:  每个 tick 检查 db/cron.json 的 mtime，用户/工具增删改后
           自动重新加载任务表并重算触发时间。
- 补漏策略: 不回补。重启或停摆期间错过的时点直接跳过，只计算下一次
           未来触发。
- 执行:    到期任务封装为 TimerEvent 投递到 EventBus，由 EventBroker
           路由到 CronChannel.handle_event 执行（system→子进程 / agent→编排器）。
           调度线程不持有通道引用、不直接执行任务 —— 触发与执行解耦，
           未来接入 Thinking Channel 时无需改动本调度器。
"""

import threading
from datetime import datetime
from typing import Any, Dict, Optional

from modules.channels.cron import store
from modules.events import TimerEvent, get_event_bus
from modules.utils.logger import log_error, log_info

_TICK_SECONDS = 10


class CronScheduler:
    """定时任务调度器单例（经 get_scheduler() 获取）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._next_run: Dict[str, datetime] = {}   # cron_id → 下次触发时刻
        self._last_mtime: float = -1.0
        self._last_error: Optional[str] = None

    # ── 生命周期 ───────────────────────────────────────────────────────

    def start(self) -> bool:
        """启动调度线程；已在运行则不做任何事。返回是否真正启动。"""
        with self._lock:
            if self.is_started:
                log_info("[CronScheduler] Already started — ignore")
                return False
            self._stop_event.clear()
            self._last_error = None
            self._reschedule_locked()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="cron-scheduler"
            )
            self._thread.start()
            log_info(
                f"[CronScheduler] Started (tick={_TICK_SECONDS}s, "
                f"tasks={len(self._next_run)})"
            )
            return True

    def stop(self) -> bool:
        """停止调度线程；已停止则不做任何事。返回是否真正停止。"""
        with self._lock:
            if not self.is_started:
                log_info("[CronScheduler] Already stopped — ignore")
                return False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
            self._next_run = {}
        if thread and thread.is_alive():
            thread.join(timeout=10)
        log_info("[CronScheduler] Stopped")
        return True

    @property
    def is_started(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    def get_status(self) -> Dict[str, Any]:
        """当前状态摘要：started/stopped、任务数与最近一次全局下次触发时间。"""
        tasks = store.load_tasks()
        with self._lock:
            next_runs = dict(self._next_run)
            error = self._last_error
        next_run = min(next_runs.values()).strftime("%Y-%m-%d %H:%M:%S") if next_runs else None
        return {
            "status": "started" if self.is_started else "stopped",
            "task_count": len(tasks),
            "enabled_count": sum(1 for t in tasks if t.get("enabled", True)),
            "next_run": next_run,
            "error": error or "",
        }

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
                log_error(f"[CronScheduler] Tick error: {e}")

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
                    f"[CronScheduler] Firing '{task['title']}' "
                    f"({task['id']}, type={task['task_type']}) → EventBus"
                )
                get_event_bus().publish(TimerEvent(task=task))
                with self._lock:
                    nxt = store.next_occurrence(task, datetime.now())
                    if nxt is not None:
                        self._next_run[task_id] = nxt
            else:
                log_info(f"[CronScheduler] Task {task_id} removed/disabled — skip")

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
        log_info(
            f"[CronScheduler] Rescheduled {len(self._next_run)} task(s) "
            f"(mtime={self._last_mtime:.0f})"
        )


# ── 进程级单例 ─────────────────────────────────────────────────────────────

_scheduler: Optional[CronScheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> CronScheduler:
    """获取进程级唯一的调度器实例（plugins 与通道共用同一状态）。"""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = CronScheduler()
        return _scheduler
