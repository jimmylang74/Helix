#!/usr/bin/env python3
"""环境对比诊断脚本。

在目标机器上运行（用目标机器的 venv）：
    python3 mcp/flight_search/env_check.py

输出 JSON 键值对，与另一环境的输出 diff 即可定位差异：
    python3 mcp/flight_search/env_check.py > env_a.json
    python3 mcp/flight_search/env_check.py > env_b.json
    diff env_a.json env_b.json
"""

import glob
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from typing import Any


def sh(cmd: str, timeout: int = 30) -> str:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return (r.stdout or "").strip()[:800]
    except Exception as e:
        return f"ERROR: {e}"


out: dict[str, Any] = {}

# ---- 系统 ----
system: dict[str, Any] = {
    "python_version": sys.version.split()[0],
    "python_exe": sys.executable,
    "os": f"{platform.system()} {platform.release()}",
    "machine": platform.machine(),
    "cpu_cores": os.cpu_count(),
    "load_avg_1_5_15": [round(x, 2) for x in os.getloadavg()],
}
try:
    with open("/proc/meminfo") as f:
        mem = {}
        for line in f:
            k, v = line.split(":", 1)
            mem[k] = v.strip()
    system["mem_total_kb"] = mem.get("MemTotal")
    system["mem_available_kb"] = mem.get("MemAvailable")
except Exception as e:
    system["mem_error"] = str(e)[:200]
out["system"] = system

# ---- Playwright 包 ----
playwright_pkg: dict[str, Any] = {
    "version": sh("python3 -m pip show playwright | grep -E '^(Version|Location):'"),
    "install_dry_run": sh("python3 -m playwright install --dry-run chromium 2>&1"),
}
try:
    playwright_pkg["import_version"] = importlib.metadata.version("playwright")
except Exception as e:
    playwright_pkg["import_version"] = f"ERROR: {e}"
out["playwright_pkg"] = playwright_pkg

# ---- Chromium 二进制 ----
cache_roots = [os.path.expanduser("~/.cache/ms-playwright")]
if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    cache_roots.insert(0, os.environ["PLAYWRIGHT_BROWSERS_PATH"])
browser_install: dict[str, Any] = {
    "playwright_browsers_path_env": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "(未设置)"),
    "cache_dirs": [
        os.path.basename(d)
        for root in cache_roots
        for d in sorted(glob.glob(os.path.join(root, "chrom*")))
    ]
    or ["(未找到任何 chromium 缓存目录)"],
}
found_exes: list[str] = []
for root in cache_roots:
    for pattern in (
        os.path.join(root, "chromium-*/chrome-linux*/chrome*"),
        os.path.join(root, "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"),
        os.path.join(root, "chromium_headless_shell-*/chrome-linux/headless_shell"),
    ):
        for exe in glob.glob(pattern):
            if os.path.isfile(exe) and os.access(exe, os.X_OK):
                found_exes.append(exe)
executables: dict[str, Any] = {}
for exe in found_exes:
    executables[os.path.basename(os.path.dirname(os.path.dirname(exe)))] = {
        "path": exe,
        "version": sh(f'"{exe}" --version 2>&1'),
        "size_mb": round(os.path.getsize(exe) / 1048576, 1),
    }
browser_install["executables"] = executables

# ---- 基础功能实测：启动 + 页面 evaluate 计时 ----
func_test: dict[str, Any] = {}
try:
    from playwright.sync_api import sync_playwright

    t_launch0 = time.time()
    with sync_playwright() as p:
        browser_install["executable_used"] = p.chromium.executable_path
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        t_launch1 = time.time()
        func_test["chromium_version"] = b.version
        pg = b.new_page()
        t_goto0 = time.time()
        pg.goto(
            "data:text/html,<html><body><div id=t>x</div></body></html>",
            wait_until="domcontentloaded",
        )
        t_goto1 = time.time()
        v = pg.evaluate("document.getElementById('t').textContent")
        t_eval1 = time.time()
        b.close()
    func_test["launch_s"] = round(t_launch1 - t_launch0, 2)
    func_test["goto_data_url_s"] = round(t_goto1 - t_goto0, 2)
    func_test["evaluate_s"] = round(t_eval1 - t_goto1, 2)
    func_test["evaluate_value"] = v
except Exception as e:
    func_test["error"] = str(e)[:400]
out["browser_install"] = browser_install
out["func_test"] = func_test

# ---- 进程与 GPU ----
out["processes_gpu"] = {
    "chrome_process_count": sh("pgrep -c -f '[c]hrome|[c]hromium' || true"),
    "chrome_processes": sh("pgrep -a -f '[c]hrome|[c]hromium' | head -5"),
    "nvidia_smi": sh(
        "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'no nvidia-smi'"
    ),
    "vga_hw": sh("lspci 2>/dev/null | grep -iE 'vga|3d|display' || echo 'no lspci'"),
}

print(json.dumps(out, indent=2, ensure_ascii=False))
