#!/usr/bin/env bash
# 交易日 14:30 外部调度入口：调用现有 tea seed-plan（方案 A，零代码改动）。
#
# 供 macOS launchd 或手动 cron 调用；不自动下单，不替代 plan-check / run。
#
# 用法：
#   ./ops/seed-plan-cron.sh
#
# 环境变量（与 run.sh 一致）：
#   TEA_HOME     数据目录（默认仓库根）
#   TEA_PYTHON   Python 解释器（默认 python3 → python）

set -euo pipefail

case "${BASH_SOURCE[0]}" in
    */*) SELF_DIR="${BASH_SOURCE[0]%/*}" ;;
    *)   SELF_DIR="." ;;
esac
ROOT="$(cd -- "$SELF_DIR/.." && pwd)"
export TEA_HOME="${TEA_HOME:-$ROOT}"
cd -- "$ROOT"

LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/seed-cron.log"
mkdir -p "$LOG_DIR"

ts() { date "+%Y-%m-%d %H:%M:%S %z"; }

# 周末跳过（与 tea.core.utils.is_trading_day 周口径一致；法定假日仍可能误触发，可手动忽略）
dow="$(date +%u)"
if [ "$dow" -gt 5 ]; then
    echo "$(ts) skip: weekend (dow=$dow)" >>"$LOG_FILE"
    exit 0
fi

PY="${TEA_PYTHON:-}"
if [ -z "$PY" ]; then
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    done
fi
if [ -z "$PY" ]; then
    echo "$(ts) error: python not found" >>"$LOG_FILE"
    exit 1
fi

echo "$(ts) start seed-plan (TEA_HOME=$TEA_HOME)" >>"$LOG_FILE"
if "$PY" -m tea seed-plan >>"$LOG_FILE" 2>&1; then
    echo "$(ts) done seed-plan exit=0" >>"$LOG_FILE"
    exit 0
fi
rc=$?
echo "$(ts) done seed-plan exit=$rc" >>"$LOG_FILE"
exit "$rc"
