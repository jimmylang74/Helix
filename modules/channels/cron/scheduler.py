"""
CronScheduler — Helix 自维护的定时任务调度器（区别于系统 crond）。

- start(): 启动独立 daemon 线程，按 tick 周期扫描到期任务并派发执行；
           幂等（已 started 再调直接返回）。
- stop():  停止调度线程；幂等。运行中的任务 worker 不受影响（自然跑完）。
- 热重载:  每个 tick 检查 db/cron.json 的 mtime，用户/工具增删改后
           自动重新加载任务表并重算触发时间。
- 补漏策略: 不回补。重启或停摆期间错过的时点直接跳过，只计算下一次
           未来触发。
- 执行:    每次触发生成独立 worker 线程：
             system 任务 → 子进程执行 shell 命令（cwd=项目根）
             agent 任务  → 经本通道私有 orchestrator.process_request 执行
           结果统一写入 db/cron.db（store.save_result）。
"""

import subprocess
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from modules.channels.cron import store
from modules.config.config_manager import ConfigManager
from modules.utils.logger import log_error, log_info, log_tool_call, log_warning

_TICK_SECONDS = 10


def format_result_message(record: Dict[str, Any], max_output: int = 5000) -> str:
    """把一条 cron 运行结果格式化为适合 IM 推送的纯文本消息。"""
    status = "成功" if record["status"] == "success" else "失败"
    lines = [
        "【定时任务执行结果】",
        f"任务: {record['title']}",
        f"任务ID: {record['cron_id']}",
        f"类型: {record['task_type']}",
        f"状态: {status}",
        f"开始: {record['started_at']}",
        f"结束: {record['finished_at']}",
        f"耗时: {record['duration_ms']} ms",
    ]
    output = (record.get("output") or "").strip()
    if output:
        if len(output) > max_output:
            output = output[:max_output] + "\n…（输出过长，已截断）"
        lines.append("─────────────────")
        lines.append(output)
    error = (record.get("error") or "").strip()
    if error:
        lines.append("─────────────────")
        lines.append(f"[错误] {error}")
    return "\n".join(lines)


class CronScheduler:
    """定时任务调度器单例（经 get_scheduler() 获取）。"""

    def __init__(self):
        self._channel: Any = None          # CronChannel，bind() 后可取 runtime.orchestrator
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._next_run: Dict[str, datetime] = {}   # cron_id → 下次触发时刻
        self._last_mtime: float = -1.0
        self._last_error: Optional[str] = None

    # ── 生命周期 ───────────────────────────────────────────────────────

    def bind(self, channel: Any) -> None:
        """绑定所属通道（agent 任务经 channel.runtime.orchestrator 执行）。"""
        self._channel = channel

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

        # 3. 派发执行并重算各自的下次触发时间
        for task_id in fired_ids:
            task = store.get_task(task_id)
            if task is not None and task.get("enabled", True):
                self._fire(task)
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

    # ── 任务执行 ───────────────────────────────────────────────────────

    def _fire(self, task: Dict[str, Any]) -> None:
        log_info(
            f"[CronScheduler] Firing '{task['title']}' ({task['id']}, "
            f"type={task['task_type']})"
        )
        threading.Thread(
            target=self._run_task,
            args=(task,),
            daemon=True,
            name=f"cron-{task['id']}",
        ).start()

    def _run_task(self, task: Dict[str, Any]) -> None:
        """worker：执行单个任务并把结果写入 db/cron.db。"""
        started_at = datetime.now()
        status, output, error = "success", "", ""
        try:
            if task["task_type"] == "system":
                status, output, error = self._run_system(task)
            else:
                status, output, error = self._run_agent(task)
        except Exception as e:  # 兜底：任何异常都落一条 failed 记录
            status, error = "failed", f"{type(e).__name__}: {e}"
            log_error(f"[CronScheduler] Task {task['id']} crashed: {e}")
        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        record = store.save_result(
            cron_id=task["id"],
            title=task["title"],
            task_type=task["task_type"],
            status=status,
            started_at=started_at.strftime("%Y-%m-%d %H:%M:%S"),
            finished_at=finished_at.strftime("%Y-%m-%d %H:%M:%S"),
            duration_ms=duration_ms,
            output=output,
            error=error,
        )
        log_tool_call(
            f"[cron] {task['id']} '{task['title']}' → {status} "
            f"({duration_ms}ms, result={record['result_id']})"
        )

        # 输出通道推送（尽力而为，失败仅记日志，不影响结果落库）
        channels = task.get("output_channels") or []
        if channels:
            from modules.channels.dispatcher import get_dispatcher

            message = format_result_message(record)
            for ch in channels:
                outcome = get_dispatcher().send(ch, message)
                if outcome.get("ok"):
                    log_info(
                        f"[CronScheduler] Result pushed to output channel "
                        f"'{ch}' ({record['result_id']})"
                    )
                else:
                    log_warning(
                        f"[CronScheduler] Push to output channel '{ch}' failed "
                        f"for {record['result_id']}: {outcome.get('error')}"
                    )

    def _run_system(self, task: Dict[str, Any]):
        """system 任务：子进程执行 shell 命令，超时可配（cron.system_timeout 秒）。"""
        timeout = ConfigManager().get("cron.system_timeout", 300)
        from modules.utils.paths import PROJECT_ROOT

        try:
            proc = subprocess.run(
                task["description"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=PROJECT_ROOT,
            )
            output = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            if proc.returncode == 0:
                return "success", output or "(无输出)", ""
            detail = f"exit code {proc.returncode}"
            if stderr:
                detail += f": {stderr[:2000]}"
            return "failed", output, detail
        except subprocess.TimeoutExpired:
            return "failed", "", f"命令超时（>{timeout}s）"

    def _run_agent(self, task: Dict[str, Any]):
        """agent 任务：把任务描述交给本通道私有 orchestrator 执行。"""
        runtime = getattr(self._channel, "runtime", None) if self._channel else None
        if runtime is None:
            return "failed", "", "Cron 通道运行时尚未装配，无法执行 agent 任务"
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        result = runtime.orchestrator.process_request(task["description"], request_id)
        final = (result.get("final_result") or "").strip()
        err = result.get("error")
        if err:
            return "failed", final, str(err)
        return "success", final or "(无输出)", ""


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
