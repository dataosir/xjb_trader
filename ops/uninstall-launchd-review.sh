#!/usr/bin/env bash
# 卸载 com.tea.review launchd 定时任务

set -euo pipefail

LABEL="com.tea.review"
AGENT_DIR="${HOME}/Library/LaunchAgents"
PLIST_DST="${AGENT_DIR}/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
    || launchctl unload "$PLIST_DST" 2>/dev/null \
    || true

if [ -f "$PLIST_DST" ]; then
    rm -f "$PLIST_DST"
fi

echo "已卸载 launchd：$LABEL"
