"""
Thinking WebHook HTTP 端点（Flask blueprint）。

- GET  /api/thinking/webhook : 连通性检查（返回 enabled / path 信息）
- POST /api/thinking/webhook : 触发一次思考事件（JSON payload，可选
  X-Webhook-Secret 校验；详见 WebhookEventSource.handle_request）

同一 blueprint 同时注册进 RPC 服务与 Admin 两个 Flask app（任一端口均可回调）。
"""

from typing import Any, Dict

from flask import Blueprint, jsonify, request

from modules.channels.thinking import store as thinking_store
from modules.channels.thinking.sources import get_webhook_source
from modules.utils.logger import log_error, log_warning

thinking_bp = Blueprint("thinking_webhook", __name__)


@thinking_bp.route("/api/thinking/webhook", methods=["GET", "POST"])
def thinking_webhook() -> Any:
    """WebHook 回调入口：GET 连通检查 / POST 触发思考事件。"""
    if request.method == "GET":
        cfg = thinking_store.load_config()["webhook"]
        return jsonify(
            {
                "ok": True,
                "path": cfg.get("path", thinking_store.WEBHOOK_DEFAULT_PATH),
                "enabled": cfg.get("enabled", True),
            }
        )

    try:
        payload = request.get_json(silent=True)
    except Exception as e:  # 非 JSON 体 → 拒绝（防御性）
        log_warning(f"[webhook] Bad request body: {e}")
        payload = None
    if not isinstance(payload, dict):
        payload = {}

    try:
        outcome: Dict[str, Any] = get_webhook_source().handle_request(
            payload, dict(request.headers)
        )
    except Exception as e:  # 源侧异常 → 500，绝不裸抛
        log_error(f"[webhook] handle_request failed: {e}")
        outcome = {"ok": False, "status": 500, "error": f"internal error: {e}"}

    status = int(outcome.pop("status", 200))
    return jsonify(outcome), status


__all__ = ["thinking_bp"]