"""
User question broker — 支持工具在节点执行过程中向用户提问并等待回答。

ask_user 工具调用会阻塞当前节点执行线程，直到以下任一情况发生：
- 用户通过 /api/rpc agent/router 提交回答（request_id + answer）
- 请求被取消（cancel_request / 任务结束）
- 超过 llm.ask_user_timeout 秒（默认 600）超时
"""

import threading
import time
from typing import Dict, Optional

from modules.config.config_manager import ConfigManager


class UserQuestionBroker:
    """按 request_id 管理待回答的用户问题，跨线程等待/应答。"""

    def __init__(self):
        self._pending: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def ask(self, request_id: str, question: str) -> str:
        """登记一个问题并阻塞等待用户回答，返回包含回答的结果文本。"""
        pending = {
            "question": question,
            "answer": "",
            "event": threading.Event(),
            "cancelled": False,
        }
        with self._lock:
            self._pending[request_id] = pending

        timeout = ConfigManager().get("llm.ask_user_timeout", 600)
        answered = pending["event"].wait(timeout)

        with self._lock:
            self._pending.pop(request_id, None)

        if pending["cancelled"]:
            return "ask_user 已取消（请求被取消或任务结束）"
        if not answered:
            return "ask_user 超时：等待用户回答超时，请基于已有信息继续任务"
        answer = pending["answer"].strip()
        if not answer:
            return "ask_user：用户未提供有效回答"
        return f"问题: {question}\n用户回复: {answer}"

    def answer(self, request_id: str, answer: str) -> bool:
        """用户提交回答，唤醒对应请求的等待线程。返回是否成功投递。"""
        with self._lock:
            pending = self._pending.get(request_id)
            if not pending or pending["event"].is_set():
                return False
            pending["answer"] = answer
            pending["event"].set()
            return True

    def cancel(self, request_id: str) -> bool:
        """取消等待中的问题（请求被取消/结束时调用）。返回是否已取消。"""
        with self._lock:
            pending = self._pending.get(request_id)
            if not pending or pending["event"].is_set():
                return False
            pending["cancelled"] = True
            pending["event"].set()
            return True

    def is_waiting(self, request_id: str) -> bool:
        with self._lock:
            return request_id in self._pending


user_question_broker = UserQuestionBroker()
