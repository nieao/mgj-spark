#!/usr/bin/env bash
# mgj-spark 启动脚本（Ubuntu 24.04 / DGX Spark aarch64）
# 用法：./start.sh   （首次自动建 venv 装依赖；前台跑。守护交给 systemd，见 systemd/）
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

# 1. 载入 .env（观测端点/模型旋钮）
if [ -f .env ]; then
  set -a; . ./.env; set +a
  echo "[1/3] 已载入 .env"
else
  echo "[1/3] 未见 .env（用默认值）。可 cp .env.spark.example .env 后改。"
fi

# 2. venv + 依赖
if [ ! -d .venv ]; then
  echo "[2/3] 创建 venv 并装依赖..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
else
  echo "[2/3] 复用已有 .venv"
fi
PY="$ROOT/.venv/bin/python"

# 3. 体检后启动
echo "[3/3] 体检..."
"$PY" bridge.py --selftest || echo "  （体检有告警，仍尝试启动——飞书凭证/服务端点见上）"
echo "------------------------------------------------------------"
echo " mgj-spark 桥启动（消费飞书消息）。Ctrl-C 停止；生产用 systemd。"
echo "------------------------------------------------------------"
exec "$PY" bridge.py
