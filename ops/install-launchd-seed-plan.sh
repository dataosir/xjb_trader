#!/usr/bin/env bash
# 安装 macOS launchd 定时任务：交易日 14:30 跑 ops/seed-plan-cron.sh
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
TEMPLATE="${ROOT}/ops/com.tea.seed-plan.plist.template"

if [ ! -f "$TEMPLATE" ]; then
    echo "找不到模板：$TEMPLATE" >&2
    exit 1
fi

chmod +x "${ROOT}/ops/seed-plan-cron.sh"
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

# 先卸载旧版（忽略失败）
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
echo "  触发：周一至周五 14:30（直接 python -m tea seed-plan，绕过 shell 脚本权限问题）"
echo "  日志：${TEA_HOME}/logs/tea.log（结构化） / seed-cron.log（手动 wrapper）"
echo "  验证：launchctl list | grep ${LABEL}"
