#!/usr/bin/env bash
# 安装 macOS launchd 定时任务：每分钟跑 tea watch-alert（盘中守卫在命令内）
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
TEMPLATE="${ROOT}/ops/com.tea.watch-alert.plist.template"

if [ ! -f "$TEMPLATE" ]; then
    echo "找不到模板：$TEMPLATE" >&2
    exit 1
fi

chmod +x "${ROOT}/ops/watch-alert-cron.sh"
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
echo "  触发：每 60 秒（命令内守卫：交易日 + 盘中 09:30–15:00）"
echo "  日志：${TEA_HOME}/logs/tea.log（tea.alert） / watch-alert-cron.log（手动 wrapper）"
echo "  验证：launchctl list | grep ${LABEL}"
echo "  邮箱：请先 config set alert.enabled / notify.email.*（默认 SMTP smtp.163.com:465）"
