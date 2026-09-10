#!/usr/bin/env bash
# 盘中每分钟外部调度入口：调用 tea watch-alert（F16，只发邮件不下单）。
#
# 供 macOS launchd（StartInterval=60）或手动试跑。
#
# 用法：
#   ./ops/watch-alert-cron.sh
#
# 环境变量（与 seed-plan-cron.sh 一致）：
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

# shellcheck source=ops/_log-daily.sh
source "${ROOT}/ops/_log-daily.sh"

ts() { date "+%Y-%m-%d %H:%M:%S %z"; }

PY="${TEA_PYTHON:-}"
if [ -z "$PY" ]; then
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    done
fi
if [ -z "$PY" ]; then
    echo "$(ts) error: python not found" >&2
    exit 1
fi

# 静默 skip 不落盘；有输出（发信/失败/候选）时 Python 写入 logs/daily/watch_alert/日期.log
OUT="$("$PY" -m tea watch-alert 2>&1)" || rc=$?
rc=${rc:-0}
if [ -n "$OUT" ]; then
    LOG_FILE="$(daily_log_resolve watch_alert)"
    daily_log_ensure_dir "$LOG_FILE"
    daily_log_prune watch_alert
    echo "$(ts) watch-alert" >>"$LOG_FILE"
    echo "$OUT" >>"$LOG_FILE"
    echo "$(ts) done watch-alert exit=$rc" >>"$LOG_FILE"
fi
exit "$rc"
