#!/usr/bin/env bash
# 一键重装全部 TEA launchd 任务（仓库迁移 / Python 升级后执行）
#
# 用法：
#   cd /path/to/tea
#   ./ops/install-launchd-all.sh
#
# 可选：TEA_HOME=~/my_trade TEA_PYTHON=/path/to/python3 ./ops/install-launchd-all.sh

set -euo pipefail

case "${BASH_SOURCE[0]}" in
    */*) SELF_DIR="${BASH_SOURCE[0]%/*}" ;;
    *)   SELF_DIR="." ;;
esac
ROOT="$(cd -- "$SELF_DIR/.." && pwd)"
TEA_HOME="${TEA_HOME:-$ROOT}"

# shellcheck source=_launchd-common.sh
source "${ROOT}/ops/_launchd-common.sh"
launchd_find_python

for script in seed-plan review watch-alert weekly-email; do
    echo "==> install-launchd-${script}.sh"
    "${ROOT}/ops/install-launchd-${script}.sh"
done

launchd_clear_stderr

echo ""
echo "全部 launchd 任务已重装。"
echo "  验证：tea launchd doctor"
echo "  或：  launchctl list | grep com.tea"
