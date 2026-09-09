#!/usr/bin/env bash
# 安装 macOS launchd 定时任务：工作日盘后跑 tea review --scheduled
# 触发时刻见 tea_config.json → scheduler.review（默认 15:01）
#
# 用法：
#   ./ops/install-launchd-review.sh
#
# 卸载：
#   ./ops/uninstall-launchd-review.sh

set -euo pipefail

case "${BASH_SOURCE[0]}" in
    */*) SELF_DIR="${BASH_SOURCE[0]%/*}" ;;
    *)   SELF_DIR="." ;;
esac
ROOT="$(cd -- "$SELF_DIR/.." && pwd)"
TEA_HOME="${TEA_HOME:-$ROOT}"
LABEL="com.tea.review"
AGENT_DIR="${HOME}/Library/LaunchAgents"
PLIST_DST="${AGENT_DIR}/${LABEL}.plist"

# shellcheck source=_launchd-common.sh
source "${ROOT}/ops/_launchd-common.sh"

chmod +x "${ROOT}/ops/review-cron.sh"
mkdir -p "$AGENT_DIR" "${TEA_HOME}/logs"

launchd_find_python
launchd_bootout_if_loaded "$LABEL" "$PLIST_DST"
launchd_render_plist review "$PLIST_DST"
launchd_bootstrap "$LABEL" "$PLIST_DST"

echo "已安装 launchd：$PLIST_DST"
echo "  TEA_HOME=$TEA_HOME"
echo "  触发：$("$PY" -c "from tea.config.schedules import trigger_summary; print(trigger_summary(None, 'review'))")"
echo "  日志：${TEA_HOME}/logs/tea.log（tea.review） / review-cron.log"
echo "  验证：launchctl list | grep ${LABEL}"
echo "  配置：tea config set review.scheduled_enabled true"
echo "  改时刻：tea config set scheduler.review.hour 15 && tea config set scheduler.review.minute 1"
echo "  手动：./ops/review-cron.sh  或  tea review --scheduled --force"
