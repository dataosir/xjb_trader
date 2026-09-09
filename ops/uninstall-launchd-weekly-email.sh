#!/usr/bin/env bash
# 卸载 macOS launchd 定时任务：com.tea.weekly-email

set -euo pipefail

LABEL="com.tea.weekly-email"
AGENT_DIR="${HOME}/Library/LaunchAgents"
PLIST_DST="${AGENT_DIR}/${LABEL}.plist"

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
        || launchctl unload "$PLIST_DST" 2>/dev/null \
        || true
fi

if [ -f "$PLIST_DST" ]; then
    rm -f "$PLIST_DST"
    echo "已卸载：$PLIST_DST"
else
    echo "未找到已安装的 plist：$PLIST_DST"
fi
