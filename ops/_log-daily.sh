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

# 集中错误日志 logs/error.log（cron/launchd 失败、Python 未捕获异常等）
error_log_append() {
    local source="$1"
    shift
    if [ -z "${PY:-}" ]; then
        echo "error_log_append: PY not set" >&2
        return 1
    fi
    "$PY" -c "import sys; from tea.core.logger import append_error_log; append_error_log(sys.argv[2], source=sys.argv[1])" _ "$source" "$*"
}

# launchd stderr 增量同步到 logs/error.log（cron 失败时可顺带扫 stderr）
stderr_sync() {
    if [ -z "${PY:-}" ]; then
        echo "stderr_sync: PY not set" >&2
        return 1
    fi
    "$PY" -c "from tea.reporting.ops_summary import sync_launchd_stderr; from tea.config.config_store import load_config; sync_launchd_stderr(load_config())"
}
