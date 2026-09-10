# shellcheck shell=bash
# launchd 安装公共函数：plist 由 tea launchd render-plist 按 scheduler.* 生成。
#
# 用法（在 install-launchd-*.sh 中）：
#   source "${ROOT}/ops/_launchd-common.sh"
#   launchd_find_python
#   launchd_render_plist review "$PLIST_DST"
#   launchd_bootstrap "$LABEL" "$PLIST_DST"

launchd_find_python() {
    PY="${TEA_PYTHON:-}"
    if [ -z "$PY" ]; then
        for cand in python3 python; do
            if command -v "$cand" >/dev/null 2>&1; then
                PY="$(command -v "$cand")"
                break
            fi
        done
    fi
    if [ -z "$PY" ]; then
        echo "找不到 python3，请设置 TEA_PYTHON" >&2
        exit 1
    fi
    if ! "$PY" -c "import tea" >/dev/null 2>&1; then
        echo "Python $PY 无法 import tea，请设置 TEA_PYTHON 为已安装本项目的解释器" >&2
        exit 1
    fi
}

launchd_render_plist() {
    local job="$1"
    local dst="$2"
    "$PY" -m tea launchd render-plist "$job" \
        --tea-home "$TEA_HOME" \
        --python "$PY" \
        -o "$dst"
}

launchd_bootout_if_loaded() {
    local label="$1"
    local plist_dst="$2"
    if launchctl list 2>/dev/null | grep -q "$label"; then
        launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null \
            || launchctl unload "$plist_dst" 2>/dev/null \
            || true
    fi
}

launchd_bootstrap() {
    local label="$1"
    local plist_dst="$2"
    launchctl bootstrap "gui/$(id -u)" "$plist_dst" 2>/dev/null \
        || launchctl load "$plist_dst"
}

# 重装后清空旧版 bash/错误 Python 遗留的 stderr，避免 sync 进 error.log 误报
launchd_clear_stderr() {
    "$PY" -c "
from tea.config.config_store import load_config
from tea.config.launchd_doctor import clear_launchd_stderr_logs
n = clear_launchd_stderr_logs(load_config()).get('count', 0)
if n:
    print(f'已清空 {n} 个 launchd stderr 日志（旧误报）')
" 2>/dev/null || true
}
