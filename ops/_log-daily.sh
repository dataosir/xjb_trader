# shellcheck shell=bash
# 操作日录日志公共函数：路径 logs/daily/{category}/YYYY-MM-DD.log
# 当天无写入则不创建文件（由 Python append_daily_log 保证）。

daily_log_resolve() {
    local category="$1"
    if [ -z "${PY:-}" ]; then
        echo "daily_log_resolve: PY not set" >&2
        return 1
    fi
    "$PY" -c "from tea.core.logger import daily_log_path; print(daily_log_path('${category}'))"
}

daily_log_ensure_dir() {
    local path="$1"
    mkdir -p "$(dirname "$path")"
}

# 清理超过 logs.daily_backup_days（默认 7）的旧日录；cron shell 直写前调用。
daily_log_prune() {
    local category="$1"
    if [ -z "${PY:-}" ]; then
        echo "daily_log_prune: PY not set" >&2
        return 1
    fi
    "$PY" -c "from tea.core.logger import prune_daily_logs; prune_daily_logs('${category}')"
}
