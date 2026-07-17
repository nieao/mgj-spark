#!/usr/bin/env bash
# 停 mgj-spark 桥（前台/裸跑用；systemd 部署请用 systemctl stop mgj-spark）
set -uo pipefail
cd "$(dirname "$0")"
if systemctl list-units --type=service 2>/dev/null | grep -q mgj-spark; then
  echo "检测到 systemd 服务，用：sudo systemctl stop mgj-spark"
fi
LOCK="_state/bridge.lock"
if [ -f "$LOCK" ]; then
  PID="$(cat "$LOCK" 2>/dev/null || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" && echo "已停桥 pid=$PID"
  else
    echo "锁文件里的进程已不在（pid=${PID:-?}）"
  fi
  rm -f "$LOCK"
else
  pkill -f "bridge.py" 2>/dev/null && echo "已按进程名停 bridge.py" || echo "没有在跑的桥"
fi
