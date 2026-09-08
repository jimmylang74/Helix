"""
CronChannel — Helix 定时任务通道。

区别于 IM 通道：无外部消息接入，仅承载调度器生命周期与定时任务执行逻辑。
装配要点：

- start(): 启动 CronScheduler 线程（组合根在 Helix 启动后调用，
           满足"Helix 启动即拉起定时任务"）。调度器是纯事件生产者：
           到期任务封装为 TimerEvent 投递 EventBus，经 EventBroker 路由
           回本通道 handle_event() 执行 —— 触发与执行解耦
- handle_event(TimerEvent): EventBroker 分发落点 —— 为每个到期任务起
          独立 worker 线程执行（system→子进程 / agent→本通道私有编排器），
          结果落 db/cron.db 并按 output_channels 推送
- stop()/is_running: 透传调度器状态，使 ChannelManager.stop_all 生效
- send():  无推送出口 —— 任务结果统一落 db/cron.db，前端定时任务页查看
- ask_user/get_context/clear_context: 本通道装配时不注册三件套工具
  （build_channel_runtime(include_channel_tools=False)），这些落点
  正常情况下不会被触达；保留兜底返回提示文本。
"""

import subprocess
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List

from modules.channels.base import ChannelAdapter, ChannelMessage, ChannelStatus
from modules.channels.cron import store
from modules.channels.cron.scheduler import get_scheduler
from modules.config.config_manager import ConfigManager
from modules.events.base import EventBase
from modules.utils.logger import log_debug, log_error, log_info, log_tool_call, log_warning


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
        get_scheduler().start()

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

    # ── 外部事件处理器（EventBroker 路由落点）─────────────────────────

    def handle_event(self, event: EventBase) -> None:
        """EventBroker 分发入口：为每个 TimerEvent 起独立 worker 线程执行。"""
        task = event.get("task") or {}
        if not task:
            log_error("[cron] TimerEvent 缺少 task — 丢弃")
            return
        threading.Thread(
            target=self._run_task,
            args=(task,),
            daemon=True,
            name=f"cron-{task.get('id', 'unknown')}",
        ).start()

    # ── 任务执行（由调度器触发事件迁移至此）───────────────────────────

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
            log_error(f"[cron] Task {task['id']} crashed: {e}")
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
                        f"[cron] Result pushed to output channel "
                        f"'{ch}' ({record['result_id']})"
                    )
                else:
                    log_warning(
                        f"[cron] Push to output channel '{ch}' failed "
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
        runtime = self.runtime
        if runtime is None or runtime.orchestrator is None:
            return "failed", "", "Cron 通道运行时尚未装配，无法执行 agent 任务"
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        result = runtime.orchestrator.process_request(task["description"], request_id)
        final = (result.get("final_result") or "").strip()
        err = result.get("error")
        if err:
            return "failed", final, str(err)
        return "success", final or "(无输出)", ""
