#!/usr/bin/env bash
# 本机 docs/ 增量同步到 NAS（不碰 data/reports/logs/tea_config.json）。
# 用法：./ops/sync-docs-to-nas.sh
# 认证：优先 SSH 公钥；否则 export NAS_SSH_PASS=... 且安装 sshpass（brew install sshpass）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAS_HOST="${NAS_HOST:-192.168.1.72}"
NAS_PORT="${NAS_PORT:-97}"
NAS_USER="${NAS_USER:-sir}"
NAS_TEA="${NAS_TEA:-/vol1/@team/code/tea}"
DEST="${NAS_USER}@${NAS_HOST}:${NAS_TEA}"

RSYNC_SSH="ssh -p ${NAS_PORT} -o StrictHostKeyChecking=accept-new"
RSYNC_BASE=(rsync -avz --itemize-changes --exclude .DS_Store)

run_rsync() {
  local src="$1"
  local dst="$2"
  if [[ -n "${NAS_SSH_PASS:-}" ]] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="$NAS_SSH_PASS" sshpass -e "${RSYNC_BASE[@]}" -e "$RSYNC_SSH" "$src" "$dst"
  else
    "${RSYNC_BASE[@]}" -e "$RSYNC_SSH" "$src" "$dst"
  fi
}

echo "→ docs/ → ${DEST}/docs/"
run_rsync "${ROOT}/docs/" "${DEST}/docs/"

echo "→ RULES.md → ${DEST}/"
run_rsync "${ROOT}/RULES.md" "${DEST}/RULES.md"

echo "done."
