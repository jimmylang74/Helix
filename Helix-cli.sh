#!/usr/bin/env bash
# Helix CLI 便捷封装 — 提供 helix_cli bash 函数。
# 用法:
#   source Helix-cli.sh
#   helix_cli "帮我写一个斐波那契脚本"
#   helix_cli --intent thinking "回顾今天"
#   helix_cli --show-config
#   answer="$(helix_cli --no-ask '1+1=?')"   # stdout 只含最终结果

helix_cli() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    python3 "$script_dir/Helix-cli.py" "$@"
}