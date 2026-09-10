#!/usr/bin/env bash
# 工作日盘后外部调度入口：调用 tea review --scheduled（F11 自动复核）。
#
# 供 macOS launchd（周一至五，时刻见 scheduler.review，默认 15:01）或手动试跑。
#
# 用法：
#   ./ops/review-cron.sh
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

LOG_FILE="$(daily_log_resolve review)"
daily_log_ensure_dir "$LOG_FILE"
daily_log_prune review

echo "$(ts) start review --scheduled (TEA_HOME=$TEA_HOME)" >>"$LOG_FILE"
if "$PY" -m tea review --scheduled >>"$LOG_FILE" 2>&1; then
    echo "$(ts) done review exit=0" >>"$LOG_FILE"
    exit 0
fi
rc=$?
echo "$(ts) done review exit=$rc" >>"$LOG_FILE"
exit "$rc"
