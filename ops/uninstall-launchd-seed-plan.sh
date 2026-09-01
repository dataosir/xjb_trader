#!/usr/bin/env bash
# 卸载 macOS launchd 定时任务 com.tea.seed-plan

set -euo pipefail

LABEL="com.tea.seed-plan"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [ -f "$PLIST" ]; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null \
        || launchctl unload "$PLIST" 2>/dev/null \
        || true
    rm -f "$PLIST"
    echo "已卸载：$PLIST"
else
    echo "未安装（找不到 $PLIST）"
fi
