#!/usr/bin/env bash
# 安装 macOS launchd 定时任务：工作日跑 tea seed-plan
# 触发时刻见 scheduler.seed_plan（默认 14:30）
#
# 用法：
#   ./ops/install-launchd-seed-plan.sh
#
# 卸载：
#   ./ops/uninstall-launchd-seed-plan.sh

set -euo pipefail

case "${BASH_SOURCE[0]}" in
    */*) SELF_DIR="${BASH_SOURCE[0]%/*}" ;;
    *)   SELF_DIR="." ;;
esac
ROOT="$(cd -- "$SELF_DIR/.." && pwd)"
TEA_HOME="${TEA_HOME:-$ROOT}"
LABEL="com.tea.seed-plan"
AGENT_DIR="${HOME}/Library/LaunchAgents"
PLIST_DST="${AGENT_DIR}/${LABEL}.plist"

# shellcheck source=_launchd-common.sh
source "${ROOT}/ops/_launchd-common.sh"

chmod +x "${ROOT}/ops/seed-plan-cron.sh"
mkdir -p "$AGENT_DIR" "${TEA_HOME}/logs"

launchd_find_python
launchd_bootout_if_loaded "$LABEL" "$PLIST_DST"
launchd_render_plist seed_plan "$PLIST_DST"
launchd_bootstrap "$LABEL" "$PLIST_DST"

echo "已安装 launchd：$PLIST_DST"
echo "  TEA_HOME=$TEA_HOME"
echo "  Python=$PY"
echo "  触发：$("$PY" -c "from tea.config.schedules import trigger_summary; print(trigger_summary(None, 'seed_plan'))")"
echo "  日志：${TEA_HOME}/logs/tea.log（结构化） / seed-cron.log（手动 wrapper）"
echo "  验证：launchctl list | grep ${LABEL}"
echo "  改时刻：tea config set scheduler.seed_plan.hour 14 && 重装本脚本"
