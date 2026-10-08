#!/bin/sh
# cluster-prism-lite 跨平台启动器 (UCFS v1.0) — 零环境变量路径自愈
cd "$(dirname "$0")" || exit 1
exec python3 main.py "$@"
