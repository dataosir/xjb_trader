#!/usr/bin/env bash
# 安装 macOS launchd 定时任务：工作日 15:35 跑 tea review --scheduled
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
TEMPLATE="${ROOT}/ops/com.tea.review.plist.template"

if [ ! -f "$TEMPLATE" ]; then
    echo "找不到模板：$TEMPLATE" >&2
    exit 1
fi

chmod +x "${ROOT}/ops/review-cron.sh"
mkdir -p "$AGENT_DIR" "${TEA_HOME}/logs"

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
        || launchctl unload "$PLIST_DST" 2>/dev/null \
        || true
fi

sed -e "s|__TEA_HOME__|${TEA_HOME}|g" "$TEMPLATE" >"$PLIST_DST"

launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null \
    || launchctl load "$PLIST_DST"

echo "已安装 launchd：$PLIST_DST"
echo "  TEA_HOME=$TEA_HOME"
echo "  触发：周一至五 15:35（命令内守卫：交易日 + 收盘后 + 每日去重）"
echo "  日志：${TEA_HOME}/logs/tea.log（tea.review） / review-cron.log"
echo "  验证：launchctl list | grep ${LABEL}"
echo "  配置：tea config set review.scheduled_enabled true"
echo "  手动：./ops/review-cron.sh  或  tea review --scheduled --force"
