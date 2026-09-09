#!/usr/bin/env bash
# 安装 macOS launchd 定时任务：每周五 17:00 跑 tea weekly-email
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
TEMPLATE="${ROOT}/ops/com.tea.weekly-email.plist.template"

if [ ! -f "$TEMPLATE" ]; then
    echo "找不到模板：$TEMPLATE" >&2
    exit 1
fi

chmod +x "${ROOT}/ops/weekly-email-cron.sh"
mkdir -p "$AGENT_DIR" "${TEA_HOME}/logs"

PY="${TEA_PYTHON:-}"
if [ -z "$PY" ]; then
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    done
fi
if [ -z "$PY" ]; then
    echo "找不到 python3，请设置 TEA_PYTHON" >&2
    exit 1
fi

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
        || launchctl unload "$PLIST_DST" 2>/dev/null \
        || true
fi

sed -e "s|__TEA_HOME__|${TEA_HOME}|g" -e "s|__TEA_PYTHON__|${PY}|g" "$TEMPLATE" >"$PLIST_DST"

launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null \
    || launchctl load "$PLIST_DST"

echo "已安装 launchd：$PLIST_DST"
echo "  TEA_HOME=$TEA_HOME"
echo "  Python=$PY"
echo "  触发：每周五 17:00（命令内守卫：交易日 + weekly_email.enabled + 邮箱已配）"
echo "  日志：${TEA_HOME}/logs/tea.log（tea.weekly_email） / weekly-email-cron.log（手动 wrapper）"
echo "  验证：launchctl list | grep ${LABEL}"
echo "  配置：tea config set weekly_email.enabled true  （SMTP 见 tea setup-email）"
