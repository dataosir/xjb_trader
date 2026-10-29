#!/usr/bin/env bash
# Linux cron 一键安装 TEA 定时任务（飞牛 NAS / 通用 Linux）。
#
# 用法：
#   cd /path/to/tea
#   chmod +x ops/install-cron-linux.sh
#   ./ops/install-cron-linux.sh
#
# 可选环境变量：
#   TEA_HOME     数据目录（默认仓库根）
#   TEA_PYTHON   Python 解释器（默认 python3）

set -euo pipefail

case "${BASH_SOURCE[0]}" in
    */*) SELF_DIR="${BASH_SOURCE[0]%/*}" ;;
    *)   SELF_DIR="." ;;
esac
ROOT="$(cd -- "$SELF_DIR/.." && pwd)"
TEA_HOME="${TEA_HOME:-$ROOT}"
PY="${TEA_PYTHON:-python3}"

MARKER="# TEA cron (install-cron-linux.sh)"
CRON_BLOCK="${MARKER}
TEA_HOME=${TEA_HOME}
TEA_PYTHON=${PY}
*/3 * * * 1-5 cd ${TEA_HOME} && TEA_HOME=${TEA_HOME} TEA_PYTHON=${PY} ${TEA_HOME}/ops/weather-retry-cron.sh
30 14 * * 1-5 cd ${TEA_HOME} && TEA_HOME=${TEA_HOME} TEA_PYTHON=${PY} ${TEA_HOME}/ops/seed-plan-cron.sh
35 14 * * 1-5 cd ${TEA_HOME} && TEA_HOME=${TEA_HOME} TEA_PYTHON=${PY} ${TEA_HOME}/ops/winrate-scan-cron.sh
1 15 * * 1-5 cd ${TEA_HOME} && TEA_HOME=${TEA_HOME} TEA_PYTHON=${PY} ${TEA_HOME}/ops/review-cron.sh
* * * * * cd ${TEA_HOME} && TEA_HOME=${TEA_HOME} TEA_PYTHON=${PY} ${TEA_HOME}/ops/watch-alert-cron.sh
0 17 * * 5 cd ${TEA_HOME} && TEA_HOME=${TEA_HOME} TEA_PYTHON=${PY} ${TEA_HOME}/ops/weekly-email-cron.sh
# end TEA cron"

# 去掉旧块后写入新块
EXISTING="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "$EXISTING" | awk -v m="$MARKER" '
    $0 == m { skip=1; next }
    skip && $0 ~ /^# end TEA cron/ { skip=0; next }
    !skip { print }
')"
{
    printf '%s\n' "$FILTERED" | sed '/^$/d'
    echo ""
    printf '%s\n' "$CRON_BLOCK"
} | crontab -

echo "TEA cron 已安装（TEA_HOME=${TEA_HOME}）"
echo "  验证：crontab -l | grep TEA"
echo "  试跑：TEA_HOME=${TEA_HOME} ${TEA_HOME}/ops/seed-plan-cron.sh"
