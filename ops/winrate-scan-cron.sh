#!/usr/bin/env bash
# 交易日 14:35 外部调度入口：调用 tea winrate-scan --scheduled（B-P1-01 影子对照）。
#
# 供 macOS launchd 或 Linux cron 调用；只落 mode=winrate 样本，不写计划、不入观察池。
#
# 用法：
#   ./ops/winrate-scan-cron.sh
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
export TEA_LAUNCHD=1
cd -- "$ROOT"

# shellcheck source=ops/_log-daily.sh
source "${ROOT}/ops/_log-daily.sh"

ts() { date "+%Y-%m-%d %H:%M:%S %z"; }

dow="$(date +%u)"
if [ "$dow" -gt 5 ]; then
    exit 0
fi

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

_on_fail() {
    local rc="$1"
    if [ "$rc" -ne 0 ]; then
        error_log_append "winrate-scan-cron" "winrate-scan --scheduled failed exit=$rc (see logs/daily/winrate/$(date +%Y-%m-%d).log)"
        stderr_sync || true
    fi
}

LOG_FILE="$(daily_log_resolve winrate)"
daily_log_ensure_dir "$LOG_FILE"
daily_log_prune winrate

echo "$(ts) start winrate-scan --scheduled (TEA_HOME=$TEA_HOME)" >>"$LOG_FILE"
if "$PY" -m tea winrate-scan --scheduled >>"$LOG_FILE" 2>&1; then
    echo "$(ts) done winrate-scan exit=0" >>"$LOG_FILE"
    exit 0
fi
rc=$?
echo "$(ts) done winrate-scan exit=$rc" >>"$LOG_FILE"
_on_fail "$rc"
exit "$rc"
