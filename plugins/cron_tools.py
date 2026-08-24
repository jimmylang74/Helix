"""
Cron Tools Plugin — 定时任务内部工具（全局共享给所有 channel）。

提供 7 个能力，全部操作 modules.channels.cron.scheduler 的进程级单例：

  list_cron    查询定时任务（含各自下次触发时间）
  create_cron  创建定时任务
  delete_cron  按 ID 删除定时任务
  modify_cron  修改定时任务（部分字段更新）
  start_cron   启动调度器线程（已 started 直接返回成功）
  stop_cron    停止调度器线程（已 stopped 直接返回成功）
  cron_status  查询调度器状态

任务定义落 db/cron.json；运行结果由调度器写入 db/cron.db。
增删改后立即调用 scheduler.reload()，调度器同时也会经 mtime 自感知。
"""

from typing import Any, Dict

from HelixCore.tools.base import BaseTool
from modules.channels.cron.scheduler import get_scheduler
from modules.channels.cron import store
from modules.utils.logger import log_tool_call


# ── 共用辅助 ───────────────────────────────────────────────────────────────


def _format_task(task: Dict[str, Any], with_next_run: bool = True) -> str:
    """单行任务摘要，供 list_cron 输出。"""
    scheduler = get_scheduler()
    enabled = "启用" if task.get("enabled", True) else "停用"
    line = (
        f"- [{task['id']}] {task['title']} | "
        f"{store.describe_schedule(task)} | "
        f"类型:{task['task_type']} | {enabled}"
    )
    if with_next_run:
        nxt = scheduler.get_next_run(task["id"])
        line += f" | 下次触发: {nxt}" if nxt else " | 下次触发: -（调度器未运行或任务停用）"
    return line


def _describe_task_full(task: Dict[str, Any]) -> str:
    return (
        f"ID: {task['id']}\n"
        f"标题: {task['title']}\n"
        f"计划: {store.describe_schedule(task)}\n"
        f"类型: {task['task_type']}\n"
        f"描述: {task['description']}\n"
        f"状态: {'启用' if task.get('enabled', True) else '停用'}"
    )


def _reload_scheduler() -> None:
    """任务表变化后让调度器立即重载（mtime 自感知是兜底路径）。"""
    scheduler = get_scheduler()
    if scheduler.is_started:
        scheduler.reload()


# ── Tool: 查询定时任务 ────────────────────────────────────────────────────


class ListCronTool(BaseTool):
    """list_cron — 查询全部定时任务。"""

    name = "list_cron"
    description = (
        "查询 Helix 系统当前的全部定时任务列表，包含每项任务的编号、标题、"
        "执行计划、类型与下次触发时间。管理定时任务前先用此工具查看现状。"
    )
    intents = ["*"]
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> str:
        log_tool_call("list_cron()")
        tasks = store.load_tasks()
        status = get_scheduler().get_status()
        if not tasks:
            return (
                f"当前没有定时任务（调度器状态: {status['status']}）。"
                "可用 create_cron 创建。"
            )
        lines = [
            f"共 {len(tasks)} 个定时任务（启用 {status['enabled_count']} 个，"
            f"调度器: {status['status']}）:"
        ]
        lines.extend(_format_task(t) for t in tasks)
        return "\n".join(lines)


# ── Tool: 创建定时任务 ────────────────────────────────────────────────────


class CreateCronTool(BaseTool):
    """create_cron — 创建定时任务。"""

    name = "create_cron"
    description = (
        "创建 Helix 定时任务。type 为 system 时 description 是要定时执行的 "
        "shell 命令；type 为 agent 时 description 是交给智能体执行的任务描述。"
        "repeat=daily 每天 time 执行；weekly 需给 weekday（0=周一…6=周日）；"
        "monthly 需给 day_of_month（1-31）。"
    )
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "任务标题（用于展示）"},
            "time": {"type": "string", "description": "执行时间点，HH:MM 24小时制，如 09:30"},
            "repeat": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "description": "重复方式",
            },
            "weekday": {
                "type": "integer",
                "description": "repeat=weekly 时必填：0=周一 … 6=周日",
            },
            "day_of_month": {
                "type": "integer",
                "description": "repeat=monthly 时必填：1-31（超出当月天数取月末）",
            },
            "task_type": {
                "type": "string",
                "enum": ["system", "agent"],
                "description": "system=直接执行 shell 命令；agent=将描述发给智能体执行",
            },
            "description": {
                "type": "string",
                "description": "system=完整命令行；agent=任务描述（越具体越好）",
            },
            "enabled": {"type": "boolean", "description": "是否启用，默认 true"},
        },
        "required": ["title", "time", "repeat", "task_type", "description"],
    }

    def execute(
        self,
        title: str = "",
        time: str = "",
        repeat: str = "",
        weekday=None,
        day_of_month=None,
        task_type: str = "",
        description: str = "",
        enabled: bool = True,
        **kwargs,
    ) -> str:
        log_tool_call(f"create_cron(title='{title[:50]}', time={time}, repeat={repeat})")
        try:
            task = store.create_task({
                "title": title,
                "time": time,
                "repeat": repeat,
                "weekday": weekday,
                "day_of_month": day_of_month,
                "task_type": task_type,
                "description": description,
                "enabled": enabled,
            })
        except store.CronValidationError as e:
            return f"错误: 创建失败 — {e}"
        _reload_scheduler()
        return f"定时任务已创建:\n{_describe_task_full(task)}"


# ── Tool: 删除定时任务 ────────────────────────────────────────────────────


class DeleteCronTool(BaseTool):
    """delete_cron — 按编号删除定时任务。"""

    name = "delete_cron"
    description = "按任务编号（ID，形如 cron_xxxx）删除指定的 Helix 定时任务。"
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "任务编号，如 cron_a1b2c3d4"},
        },
        "required": ["id"],
    }

    def execute(self, id: str = "", **kwargs) -> str:
        log_tool_call(f"delete_cron(id={id})")
        task = store.get_task(id)
        if task is None:
            known = ", ".join(t["id"] for t in store.load_tasks()) or "(无)"
            return f"错误: 任务不存在: {id}。现有任务编号: {known}"
        store.delete_task(id)
        _reload_scheduler()
        return f"已删除定时任务 [{id}] {task['title']}"


# ── Tool: 修改定时任务 ────────────────────────────────────────────────────


class ModifyCronTool(BaseTool):
    """modify_cron — 按 ID 部分修改定时任务字段。"""

    name = "modify_cron"
    description = (
        "修改已有的 Helix 定时任务（按编号），只传需要修改的字段即可；"
        "字段含义同 create_cron。"
    )
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "任务编号"},
            "title": {"type": "string", "description": "新标题"},
            "time": {"type": "string", "description": "新时间点 HH:MM"},
            "repeat": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "description": "新重复方式",
            },
            "weekday": {"type": "integer", "description": "weekly 用：0-6"},
            "day_of_month": {"type": "integer", "description": "monthly 用：1-31"},
            "task_type": {"type": "string", "enum": ["system", "agent"]},
            "description": {"type": "string", "description": "新命令/任务描述"},
            "enabled": {"type": "boolean", "description": "启停"},
        },
        "required": ["id"],
    }

    def execute(self, id: str = "", **kwargs) -> str:
        patch = {
            k: v for k, v in kwargs.items()
            if k in (
                "title", "time", "repeat", "weekday", "day_of_month",
                "task_type", "description", "enabled",
            ) and v is not None
        }
        log_tool_call(f"modify_cron(id={id}, fields={sorted(patch)})")
        if not patch:
            return "错误: 未提供任何要修改的字段"
        try:
            task = store.update_task(id, patch)
        except store.CronValidationError as e:
            return f"错误: 修改失败 — {e}"
        except KeyError:
            known = ", ".join(t["id"] for t in store.load_tasks()) or "(无)"
            return f"错误: 任务不存在: {id}。现有任务编号: {known}"
        _reload_scheduler()
        return f"定时任务已修改:\n{_describe_task_full(task)}"


# ── Tool: 启动 / 停止 / 状态 ──────────────────────────────────────────────


class StartCronTool(BaseTool):
    """start_cron — 启动定时器独立线程（幂等）。"""

    name = "start_cron"
    description = (
        "启动 Helix 定时任务调度器线程，开始按计划执行任务。"
        "如果已经在运行则直接返回成功，不做任何事。"
    )
    intents = ["*"]
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> str:
        log_tool_call("start_cron()")
        started = get_scheduler().start()
        status = get_scheduler().get_status()
        if started:
            msg = "定时任务调度器已启动"
        else:
            msg = "调度器已在运行中，无需重复启动"
        nxt = f"，最近触发: {status['next_run']}" if status["next_run"] else ""
        return f"{msg}（状态: {status['status']}，启用任务 {status['enabled_count']} 个{nxt}）"


class StopCronTool(BaseTool):
    """stop_cron — 停止定时器线程（幂等）。"""

    name = "stop_cron"
    description = (
        "停止 Helix 定时任务调度器线程，暂停所有定时任务的自动执行"
        "（正在执行中的任务会自然完成）。如果已经停止则直接返回成功。"
    )
    intents = ["*"]
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> str:
        log_tool_call("stop_cron()")
        stopped = get_scheduler().stop()
        if stopped:
            return "定时任务调度器已停止"
        return "调度器已是停止状态，无需重复停止"


class CronStatusTool(BaseTool):
    """cron_status — 查询定时器状态（started/stopped）。"""

    name = "cron_status"
    description = (
        "查询 Helix 定时任务调度器的当前状态（started/stopped）、"
        "任务数量与下一次触发时间。"
    )
    intents = ["*"]
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> str:
        log_tool_call("cron_status()")
        s = get_scheduler().get_status()
        parts = [f"调度器状态: {s['status']}", f"任务总数: {s['task_count']}",
                 f"启用任务: {s['enabled_count']}"]
        if s["next_run"]:
            parts.append(f"下次触发: {s['next_run']}")
        if s["error"]:
            parts.append(f"最近错误: {s['error']}")
        return "；".join(parts)
