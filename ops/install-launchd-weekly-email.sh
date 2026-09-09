#!/usr/bin/env bash
# 安装 macOS launchd 定时任务：周五跑 tea weekly-email
# 触发时刻见 scheduler.weekly_email（默认周五 17:00）
#
# 用法：
#   ./ops/install-launchd-weekly-email.sh
#
# 卸载：
#   ./ops/uninstall-launchd-weekly-email.sh

set -euo pipefail

case "${BASH_SOURCE[0]}" in
    */*) SELF_DIR="${BASH_SOURCE[0]%/*}" ;;
    *)   SELF_DIR="." ;;
esac
ROOT="$(cd -- "$SELF_DIR/.." && pwd)"
TEA_HOME="${TEA_HOME:-$ROOT}"
LABEL="com.tea.weekly-email"
AGENT_DIR="${HOME}/Library/LaunchAgents"
PLIST_DST="${AGENT_DIR}/${LABEL}.plist"

# shellcheck source=_launchd-common.sh
source "${ROOT}/ops/_launchd-common.sh"

chmod +x "${ROOT}/ops/weekly-email-cron.sh"
mkdir -p "$AGENT_DIR" "${TEA_HOME}/logs"

launchd_find_python
launchd_bootout_if_loaded "$LABEL" "$PLIST_DST"
launchd_render_plist weekly_email "$PLIST_DST"
launchd_bootstrap "$LABEL" "$PLIST_DST"

echo "已安装 launchd：$PLIST_DST"
echo "  TEA_HOME=$TEA_HOME"
echo "  Python=$PY"
echo "  触发：$("$PY" -c "from tea.config.schedules import trigger_summary; print(trigger_summary(None, 'weekly_email'))")"
echo "  日志：${TEA_HOME}/logs/tea.log（tea.weekly_email） / weekly-email-cron.log（手动 wrapper）"
echo "  验证：launchctl list | grep ${LABEL}"
echo "  配置：tea config set weekly_email.enabled true  （SMTP 见 tea setup-email）"
