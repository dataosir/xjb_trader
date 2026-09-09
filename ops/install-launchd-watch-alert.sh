#!/usr/bin/env bash
# 安装 macOS launchd 定时任务：周期跑 tea watch-alert（盘中守卫在命令内）
# 间隔见 scheduler.watch_alert.interval_sec（默认 60）
#
# 用法：
#   ./ops/install-launchd-watch-alert.sh
#
# 卸载：
#   ./ops/uninstall-launchd-watch-alert.sh

set -euo pipefail

case "${BASH_SOURCE[0]}" in
    */*) SELF_DIR="${BASH_SOURCE[0]%/*}" ;;
    *)   SELF_DIR="." ;;
esac
ROOT="$(cd -- "$SELF_DIR/.." && pwd)"
TEA_HOME="${TEA_HOME:-$ROOT}"
LABEL="com.tea.watch-alert"
AGENT_DIR="${HOME}/Library/LaunchAgents"
PLIST_DST="${AGENT_DIR}/${LABEL}.plist"

# shellcheck source=_launchd-common.sh
source "${ROOT}/ops/_launchd-common.sh"

chmod +x "${ROOT}/ops/watch-alert-cron.sh"
mkdir -p "$AGENT_DIR" "${TEA_HOME}/logs"

launchd_find_python
launchd_bootout_if_loaded "$LABEL" "$PLIST_DST"
launchd_render_plist watch_alert "$PLIST_DST"
launchd_bootstrap "$LABEL" "$PLIST_DST"

echo "已安装 launchd：$PLIST_DST"
echo "  TEA_HOME=$TEA_HOME"
echo "  Python=$PY"
echo "  触发：$("$PY" -c "from tea.config.schedules import trigger_summary; print(trigger_summary(None, 'watch_alert'))")"
echo "  日志：${TEA_HOME}/logs/tea.log（tea.alert） / watch-alert-cron.log（手动 wrapper）"
echo "  验证：launchctl list | grep ${LABEL}"
echo "  邮箱：请先 config set alert.enabled / notify.email.*（默认 SMTP smtp.163.com:465）"
