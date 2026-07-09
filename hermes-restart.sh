#!/bin/bash
# hermes-restart.sh — 安全重启 Hermes CLI
# 在 SSH 会话中直接运行：./hermes-restart.sh
# 不要用 source，不要用 bash -c 包起来

set -e

HERMES_DIR="/home/nick/work/hermes-agent"
MY_PTS=$(tty 2>/dev/null | sed 's|/dev/||')
MY_PID=$$

echo "=== Hermes 一键重启 ==="

# 1. 清理其他会话留下的僵尸 hermes（排除当前终端和当前脚本）
echo "[1/3] 检查残留进程..."
STALE=$(ps aux | grep '[h]ermes chat\|[h]ermes --cli' \
    | grep -v "$MY_PTS" \
    | grep -v "$$" \
    | grep -v "$PPID" \
    | awk '{print $2}')
if [ -n "$STALE" ]; then
    echo "  发现其他会话残留: $STALE"
    for pid in $STALE; do
        kill -TERM "$pid" 2>/dev/null && echo "  终止 $pid" || true
    done
    sleep 1
else
    echo "  无残留"
fi

# 2. 激活环境
echo "[2/3] 激活虚拟环境..."
cd "$HERMES_DIR"
source .venv/bin/activate

# 3. 启动
echo "[3/3] 启动 Hermes..."
echo ""
exec hermes chat --cli --continue
