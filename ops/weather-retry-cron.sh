#!/usr/bin/env bash
# 盘中每隔数分钟重试市场天气（东财抖动时刷新板块/涨跌家数，减轻 sector_stale / hard 缺口）。
#
# 供 Linux/macOS crontab 调用；有缺口才 force 刷新，数据已齐则快速退出。
#
# 用法：
#   ./ops/weather-retry-cron.sh
#
# 环境变量（与 seed-plan-cron.sh 一致）：
#   TEA_HOME          数据目录（默认仓库根）
#   TEA_PYTHON        Python 解释器
#   TEA_WEATHER_RETRY_WINDOW  可跑时段 HHMM-HHMM（默认 0925-1510，本地时区）

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

now_hm="$(date +%H%M)"
win="${TEA_WEATHER_RETRY_WINDOW:-0925-1510}"
win_start="${win%-*}"
win_end="${win#*-}"
if [ "$now_hm" -lt "$win_start" ] || [ "$now_hm" -gt "$win_end" ]; then
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

LOG_DIR="${TEA_HOME}/logs/daily/weather_retry"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/$(date +%Y-%m-%d).log"

_on_fail() {
    local rc="$1"
    local msg="$2"
    if [ "$rc" -ne 0 ]; then
        error_log_append "weather-retry-cron" "$msg"
        stderr_sync || true
    fi
}

echo "$(ts) start weather-retry probe (TEA_HOME=$TEA_HOME window=$win)" >>"$LOG_FILE"

PROBE_OUT="$("$PY" <<'PY' 2>>"$LOG_FILE"
import sys
from tea.analysis.sentiment import data_gap_summary, get_sentiment
from tea.config.config_store import load_config
from tea.data import Market
from tea.phases.prompt import IO
from tea.runtime import runner

def needs_retry(s: dict) -> bool:
    if s.get("sector_stale"):
        return True
    if data_gap_summary(s):
        return True
    return False

cfg = load_config()
mk = Market(cfg)
io = IO(quiet=True)
s = get_sentiment(mk, cfg, force=False, io=io)
if needs_retry(s):
    s = runner.weather(cfg, mk, refresh=True, io=io)
if needs_retry(s):
    gaps = data_gap_summary(s)
    stale = bool(s.get("sector_stale"))
    print(f"still_gap stale={stale} gaps={' | '.join(gaps) if gaps else '-'}")
    sys.exit(2)
print("ok score=%s cycle=%s stance=%s cached=%s" % (
    s.get("score"), s.get("cycle"), s.get("stance"), s.get("cached")))
sys.exit(0)
PY
)" || rc=$?
rc=${rc:-0}

echo "$(ts) probe: ${PROBE_OUT:-"(no output)"} exit=$rc" >>"$LOG_FILE"

if [ "$rc" -eq 0 ]; then
    exit 0
fi
if [ "$rc" -eq 2 ]; then
    _on_fail "$rc" "market weather still has gaps (${PROBE_OUT:-see log}); will retry on next cron"
    exit 2
fi
_on_fail "$rc" "weather-retry probe failed exit=$rc (see ${LOG_FILE})"
exit "$rc"
