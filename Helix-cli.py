#!/usr/bin/env python3
"""Helix 快速测试 CLI — 终端版 Web 快速测试页面。

独立应用：通过 HTTP 远程调用已运行的 Helix 服务（Web 通道），进程内不启动
任何 HelixCore Agent 实例。与浏览器前端协议完全一致：

    POST  {base}/api/rpc                 JSON-RPC 2.0（agent/router 等）
    SSE   {base}/api/llm-stream          Thinking / 工具调用 / assistant 流
    SSE   {base}/api/status-stream       节点状态 / 最终结果流

默认 base 为 admin 端口 11556（/api/rpc 与两个 SSE 端点同源）；--host/--port
可指向其他地址。默认仅输出最终结果（→ stdout，可直接管道/命令替换）与
错误/ask_user 交互；诊断输出（LLM Sending / 原始响应 / 节点进度等）需
--verbose 开启，Thinking 默认显示、--no-thinking 关闭。--json 模式下
stdout 输出单个 JSON 对象。
"""

import argparse
import json
import sys
import threading
import uuid
import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import quote, urlparse

__version__ = "1.0.0"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11556
DEFAULT_TIMEOUT = 600  # 整体等待上限（秒）
RPC_TIMEOUT = 60       # 单次 RPC 调用超时（秒）

_NO_ASK_ANSWER = "N/A"  # --no-ask 模式下 ask_user 的自动答复

_C = {
    "dim": "\033[2m", "cyan": "\033[36m", "yellow": "\033[33m",
    "green": "\033[32m", "red": "\033[31m", "magenta": "\033[35m",
    "bold": "\033[1m", "reset": "\033[0m",
}


class RpcError(RuntimeError):
    """服务端返回的 JSON-RPC error。"""


class _RequestState:
    """请求生命周期共享状态（双 SSE 线程 + 主线程）。"""

    def __init__(self, color: bool):
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.color = color
        self.final_result = ""
        self.error = None
        self.token_usage = None
        self.generated_files: List[Any] = []
        self.llm_done = False


def _base_url(args) -> str:
    return f"http://{args.host}:{args.port}"


def _build_opener(base: str):
    """构建 urllib opener；请求 loopback 主机时绕过 http_proxy/https_proxy。"""
    host = urlparse(base).hostname
    if host in ("localhost", "127.0.0.1", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def rpc(base, method, params=None, timeout=RPC_TIMEOUT):
    """JSON-RPC 2.0 调用，返回 result 字典。"""
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex[:12],
        "method": method,
        "params": params or {},
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/rpc",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _build_opener(base).open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RpcError(f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RpcError(f"无法连接 {base}: {e.reason}") from e
    if data.get("error"):
        raise RpcError(data["error"].get("message", "RPC error"))
    return data.get("result")


def sse_events(url):
    """SSE 流迭代器：逐个 yield 解析后的 JSON 事件 dict。"""
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    try:
        resp = _build_opener(url).open(req, timeout=None)
    except urllib.error.URLError as e:
        raise RpcError(f"SSE 连接失败 {url}: {e.reason}") from e
    data_lines = []
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        pass
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())


def _color(enabled: bool, code: str, text: str) -> str:
    if not enabled:
        return text
    return f"{_C[code]}{text}{_C['reset']}"


def _handle_ask_user(base, request_id, question, no_ask, color, out):
    """ask_user 事件处理：交互读 stdin 提交回答；--no-ask 自动答复。"""
    if no_ask:
        out(_color(color, "yellow", f"[ask] (--no-ask 自动答复) {question}"))
        rpc(base, "agent/router", {"request_id": request_id, "answer": _NO_ASK_ANSWER})
        out(_color(color, "yellow", f"[ask] → {_NO_ASK_ANSWER}"))
        return
    out(_color(color, "magenta", f"❓ {question}"))
    while True:
        sys.stderr.write("答: ")
        sys.stderr.flush()
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            sys.stderr.write("\n")
            return
        if line == "":
            line = _NO_ASK_ANSWER
            out(_color(color, "yellow", f"[ask] EOF，自动答复 {_NO_ASK_ANSWER}"))
            rpc(base, "agent/router", {"request_id": request_id, "answer": line})
            return
        answer = line.strip()
        if answer:
            rpc(base, "agent/router", {"request_id": request_id, "answer": answer})
            return
        out(_color(color, "yellow", "[ask] 回答不能为空 (Ctrl+C 取消)"))


def _llm_thread(base, args, request_id, state, out):
    color = state.color
    verbose = args.verbose
    url = f"{base}/api/llm-stream?request_id={quote(request_id)}"
    try:
        for event in sse_events(url):
            etype = event.get("type", "")
            if etype in ("thinking", "thinking_delta"):
                if not args.no_thinking:
                    out(_color(color, "cyan", event.get("delta") or event.get("content") or ""), end="")
            elif etype == "thinking_end":
                if not args.no_thinking:
                    out(_color(color, "cyan", "\n"))
            elif etype in ("assistant", "assistant_delta"):
                if verbose:
                    out(_color(color, "dim", event.get("delta") or event.get("content") or ""), end="")
            elif etype == "assistant_end":
                if verbose:
                    out(_color(color, "dim", "\n"))
            elif etype == "sending":
                if verbose:
                    prov = event.get("provider", "")
                    model = event.get("model", "")
                    params_txt = ""
                    if event.get("temperature") is not None:
                        params_txt = f" (Temperature={event['temperature']}, Top_p={event.get('top_p')})"
                    out(_color(color, "yellow", f"📤 Sending to LLM: {prov}/{model}{params_txt}"))
            elif etype in ("tool_call_begin", "tool_call_start"):
                if verbose:
                    out(_color(color, "yellow", f"🔧 {event.get('name', 'tool')} ({event.get('id', '')})"))
            elif etype == "tool_call_result":
                if verbose:
                    name = event.get("name", "")
                    result = event.get("result", "")
                    out(_color(color, "green", f"  → {name} 结果: {str(result)[:200]}"))
            elif etype == "ask_user":
                _handle_ask_user(base, request_id, event.get("question", ""), args.no_ask, color, out)
            elif etype == "error":
                out(_color(color, "red",
                           f"[LLM error] {event.get('message') or event.get('error') or 'Unknown'}"))
    except RpcError as e:
        out(_color(color, "red", f"[llm-stream 断开] {e}"))
    except Exception as e:
        out(_color(color, "red", f"[llm-stream 异常] {e}"))
    finally:
        state.llm_done = True


def _status_thread(base, args, request_id, state, out):
    color = state.color
    verbose = args.verbose
    url = f"{base}/api/status-stream?request_id={quote(request_id)}&cursor=0"
    nodes = {}
    last_phase = ""
    try:
        for event in sse_events(url):
            if event.get("type") != "status":
                continue
            phase = event.get("orchestrator_phase", "")
            if phase and phase != last_phase:
                if verbose:
                    out(_color(color, "bold", f"〔{phase}〕"))
                last_phase = phase
            for node in event.get("task_graph_nodes") or []:
                nid = node.get("id") or node.get("title")
                ns = node.get("state", "")
                if not nid or (nid in nodes and nodes[nid] == ns):
                    continue
                nodes[nid] = ns
                if verbose:
                    mark = {"Running": "▶", "Done": "✓", "Failed": "✗", "Ready": "▷"}.get(ns, "·")
                    code = ("green" if ns == "Done" else "red" if ns == "Failed"
                            else "yellow" if ns == "Running" else "dim")
                    out(_color(color, code, f"  {mark} {node.get('title') or nid} [{ns}]"))
            node_result = event.get("node_result")
            if node_result and node_result.get("response"):
                if verbose:
                    title = node_result.get("node_title") or node_result.get("node_id") or "节点"
                    out(_color(color, "green", f"▶ 节点结果: {title}"))
                    for seg in str(node_result["response"]).splitlines():
                        out(f"  {seg}")
            with state.lock:
                if event.get("token_usage"):
                    state.token_usage = event["token_usage"]
                if event.get("generated_files"):
                    state.generated_files = event["generated_files"]
                if event.get("final_result"):
                    state.final_result = event["final_result"]
                if event.get("error"):
                    state.error = event["error"]
            if event.get("completed"):
                break
    except RpcError as e:
        out(_color(color, "red", f"[status-stream 断开] {e}"))
        with state.lock:
            state.error = state.error or str(e)
    except Exception as e:
        out(_color(color, "red", f"[status-stream 异常] {e}"))
        with state.lock:
            state.error = state.error or str(e)
    finally:
        state.done.set()


def _run_request(args) -> Dict[str, Any]:
    base = _base_url(args)
    result = rpc(base, "agent/router", {"request": args.request, "intent": args.intent})
    request_id = result.get("request_id")
    if not request_id:
        raise RpcError("服务端未返回 request_id")

    state = _RequestState(color=args.color)

    def out(s="", end="\n"):
        print(s, end=end, file=sys.stderr, flush=True)

    t_status = threading.Thread(
        target=_status_thread, args=(base, args, request_id, state, out), daemon=True)
    t_llm = threading.Thread(
        target=_llm_thread, args=(base, args, request_id, state, out), daemon=True)
    t_status.start()
    t_llm.start()

    try:
        if not state.done.wait(timeout=args.timeout):
            out(f"[超时] {args.timeout}s 内未完成，尝试取消服务端请求...")
            try:
                rpc(base, "agent/cancel", {"request_id": request_id}, timeout=10)
            except RpcError:
                pass
            raise RpcError(f"等待超时 ({args.timeout}s)")
    except KeyboardInterrupt:
        out("[中断] 尝试取消服务端请求...")
        try:
            rpc(base, "agent/cancel", {"request_id": request_id}, timeout=10)
        except RpcError:
            pass
        sys.exit(130)

    with state.lock:
        return {
            "success": not state.error,
            "request_id": request_id,
            "intent": args.intent,
            "final_result": state.final_result,
            "error": state.error,
            "token_usage": state.token_usage,
            "generated_files": state.generated_files,
        }


def _show_config(args):
    base = _base_url(args)
    cfg = rpc(base, "config.get").get("config", {})
    llm = cfg.get("llm", {})
    server = cfg.get("server", {})
    api_key = llm.get("api_key") or ""
    masked = (api_key[:2] + "****") if api_key else "(空)"

    print(f"LLM 配置 (Helix 服务端: {base})")
    for key in ("provider", "model", "endpoint"):
        print(f"  {key:14} {llm.get(key, '')}")
    print(f"  {'api_key':14} {masked}")
    print(f"  {'stream':14} {llm.get('stream', '')}")
    for phase in ("planning", "execution", "finalizer"):
        g = (llm.get("graph") or {}).get(phase) or {}
        if g:
            print(f"  {'graph.' + phase:14} temperature={g.get('temperature')}, top_p={g.get('top_p')}")
    print(f"  服务端口: rpc={server.get('rpc_port')} admin={server.get('admin_port')}")


def _list_providers(args):
    base = _base_url(args)
    result = rpc(base, "llm.providers")
    providers = result.get("providers") or []
    for p in providers:
        if isinstance(p, dict):
            print(f"  {str(p.get('provider', '')):24} {p.get('description', '')}")
        else:
            print(f"  {str(p)}")
    print(f"共 {len(providers)} 个供应商")


def build_parser():
    p = argparse.ArgumentParser(
        prog="Helix-cli",
        description="终端版 Helix 快速测试：远程调用已运行的 Helix 服务（Web 通道）执行 Agent 请求。",
        epilog=(
            "示例:\n"
            "  Helix-cli.py \"帮我写一个斐波那契脚本\"\n"
            "  Helix-cli.py --intent coding \"实现冒泡排序\"\n"
            "  Helix-cli.py --intent thinking \"回顾今天\"\n"
            "  Helix-cli.py --show-config\n"
            "  Helix-cli.py --list-providers\n"
            "  result=\"$(Helix-cli.py '1+1=?')\"   # stdout 只含最终结果\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("request", nargs="?", help="请求文本（--show-config / --list-providers 时省略）")
    p.add_argument("--show-config", action="store_true", help="列出服务端 LLM 配置信息后退出")
    p.add_argument("--list-providers", action="store_true", help="列出支持的 LLM 供应商后退出")
    p.add_argument("--intent", default="auto", help="强制意图（默认 auto）")
    p.add_argument("--no-thinking", action="store_true", help="屏蔽 Thinking 过程输出")
    p.add_argument("--no-ask", action="store_true", help="非交互：ask_user 自动答复 N/A")
    p.add_argument("--json", action="store_true", help="stdout 输出单个 JSON 对象")
    p.add_argument("--no-color", action="store_true", help="禁用 ANSI 颜色")
    p.add_argument("--verbose", action="store_true",
                   help="输出详细过程信息（LLM Sending/原始响应/节点进度等诊断输出，默认关闭）")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"Helix 主机（默认 {DEFAULT_HOST}）")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"Helix admin 端口（默认 {DEFAULT_PORT}，RPC+SSE 同源）")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help=f"整体等待上限秒数（默认 {DEFAULT_TIMEOUT}）")
    p.add_argument("--version", action="version", version=f"Helix-cli {__version__}")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.color = sys.stderr.isatty() and not args.no_color

    if args.show_config:
        _show_config(args)
        return
    if args.list_providers:
        _list_providers(args)
        return
    if not args.request or not args.request.strip():
        build_parser().error("需要请求文本（或使用 --show-config / --list-providers）")

    try:
        summary = _run_request(args)
    except RpcError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    ok = bool(summary.get("success"))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    elif ok:
        print(summary.get("final_result") or "")
        files = summary.get("generated_files") or []
        if files:
            print("\n生成文件:", file=sys.stderr)
            for f in files:
                print(f"  {f}", file=sys.stderr)
    else:
        print(f"错误: {summary.get('error') or '未知错误'}", file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()