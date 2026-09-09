#!/usr/bin/env bash
# 卸载 com.tea.watch-alert launchd 任务

set -euo pipefail

LABEL="com.tea.watch-alert"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
        || launchctl unload "$PLIST_DST" 2>/dev/null \
        || true
fi

if [ -f "$PLIST_DST" ]; then
    rm -f "$PLIST_DST"
fi

echo "已卸载 launchd：$LABEL"
