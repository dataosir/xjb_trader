"""运行日志：logs/ 独立目录 + 按日切割（标准库 logging，零第三方依赖）。

与 data/ 下的结构化追溯（accumulator / seed_trace / scan_details / seed_records）
不同，这里记的是「程序运行过程」：谁在何时、以什么参数、走了哪条分支、算出什么
结果。用于日后**根据日志迭代程序**，不直接参与选股决策。

- ``init_logging(cfg)``：进程启动时调用一次，把 root 日志落到 ``logs/tea.log``。
- ``get_logger(name)``：取 ``tea`` 命名空间下的 logger，直接 ``.info/.warning/.error``。
- ``daily_log_path`` / ``append_daily_log``：按**操作类型**分目录的日录日志
  （``logs/daily/{category}/YYYY-MM-DD.log``）；当天无写入则**不创建**文件；
  超过 ``logs.daily_backup_days``（默认 7）自动清理。
- ``DailyLogSession``：单次任务期间把指定 logger 同步写入当日操作日志。

日志目录不可写时静默降级（程序照常跑，只是不落运行日志），结构化数据仍走
``data/`` 目录不受影响。
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

from tea.config.config_store import Config, load_config
from tea.core import utils

_LOGGER_NAME = "tea"
_initialized = False
_pruned_categories: Set[str] = set()

# 操作日录类别 → 默认同步写入的 logger 子名（相对 tea.*）
DAILY_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "seed": ("scan", "data", "score", "veto", "ft"),
    "review": ("review", "ft"),
    "watch_alert": ("alert",),
    "weekly_email": ("weekly_email", "review"),
}

_LOG_FMT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def init_logging(cfg: Optional[Config] = None, level: int = logging.INFO) -> logging.Logger:
    """初始化运行日志（幂等），返回 root logger。

    文件 handler 装配失败不抛出，退回默认传播；目录创建失败也不抛出，直接略过
    文件日志。这样日志子系统永远不会把主流程打挂。
    """
    global _initialized
    logger = logging.getLogger(_LOGGER_NAME)
    if _initialized:
        return logger

    try:
        cfg = cfg or load_config()
        log_dir = cfg.logs_dir()
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(log_dir, "tea.log"),
            when="midnight", backupCount=int(cfg.get("logs.backup_days", 30)),
            encoding="utf-8")
        handler.suffix = "%Y-%m-%d"
        handler.setFormatter(logging.Formatter(_LOG_FMT))
        logger.setLevel(level)
        logger.propagate = False
        logger.addHandler(handler)
    except Exception:
        # 目录建不出来 / handler 装不上：不落文件，退回默认，不打断主流程。
        logger.propagate = True
    _initialized = True
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """取 tea 命名空间下的 logger；name 为空返回 root。"""
    return logging.getLogger(_LOGGER_NAME if not name else f"{_LOGGER_NAME}.{name}")


def _validate_category(category: str) -> None:
    if category not in DAILY_CATEGORIES:
        raise ValueError(f"未知日录类别: {category!r}，可选 {sorted(DAILY_CATEGORIES)}")


def _daily_backup_days(cfg: Config) -> int:
    return max(1, int(cfg.get("logs.daily_backup_days", 7)))


def prune_daily_logs(category: Optional[str] = None,
                     cfg: Optional[Config] = None) -> int:
    """删除超过保留期的操作日录；``category`` 为 None 时清理全部类别。返回删除文件数。"""
    cfg = cfg or load_config()
    keep_days = _daily_backup_days(cfg)
    today = utils.now().date()
    categories = [category] if category else list(DAILY_CATEGORIES)
    removed = 0
    for cat in categories:
        _validate_category(cat)
        log_dir = os.path.join(cfg.logs_dir(), "daily", cat)
        if not os.path.isdir(log_dir):
            continue
        for name in os.listdir(log_dir):
            if not name.endswith(".log"):
                continue
            day = utils.parse_date(name[:-4])
            if day is None:
                continue
            if (today - day).days >= keep_days:
                try:
                    os.remove(os.path.join(log_dir, name))
                    removed += 1
                except OSError:
                    pass
    return removed


def _maybe_prune_daily_logs(category: str, cfg: Config) -> None:
    """每进程每类别至多清理一次，避免高频 append 反复扫目录。"""
    key = f"{category}:{utils.today_str()}"
    if key in _pruned_categories:
        return
    _pruned_categories.add(key)
    prune_daily_logs(category, cfg)


def daily_log_path(category: str, cfg: Optional[Config] = None,
                   day: Optional[str] = None) -> str:
    """当日操作日志路径（文件可能尚不存在）。

    形如 ``logs/daily/seed/2026-09-10.log``。
    """
    _validate_category(category)
    cfg = cfg or load_config()
    day = day or utils.today_str()
    return os.path.join(cfg.logs_dir(), "daily", category, f"{day}.log")


def append_daily_log(category: str, message: str, cfg: Optional[Config] = None,
                     day: Optional[str] = None, with_ts: bool = True) -> bool:
    """追加一行到当日操作日志；首次写入时创建目录与文件。失败返回 False。"""
    if not message:
        return False
    try:
        path = daily_log_path(category, cfg, day)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = (f"{utils.now().strftime('%Y-%m-%d %H:%M:%S %z')} {message}"
                if with_ts else message)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")
        _maybe_prune_daily_logs(category, cfg or load_config())
        return True
    except OSError:
        return False


def write_daily_transcript(category: str, lines: Sequence[str],
                           cfg: Optional[Config] = None,
                           day: Optional[str] = None,
                           header: str = "--- console ---") -> bool:
    """把控制台 transcript 整段写入当日操作日志（种子扫描等完整输出留档）。"""
    if not lines:
        return False
    try:
        path = daily_log_path(category, cfg, day)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n{header}\n")
            fh.write("\n".join(lines))
            if lines[-1] != "":
                fh.write("\n")
        _maybe_prune_daily_logs(category, cfg or load_config())
        return True
    except OSError:
        return False


def _attach_daily_handler(category: str, cfg: Config) -> Tuple[logging.Handler, List[logging.Logger]]:
    path = daily_log_path(category, cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FMT))
    loggers: List[logging.Logger] = []
    for name in DAILY_CATEGORIES[category]:
        lg = get_logger(name)
        lg.addHandler(handler)
        loggers.append(lg)
    return handler, loggers


def _detach_daily_handler(handler: logging.Handler, loggers: Sequence[logging.Logger]) -> None:
    for lg in loggers:
        lg.removeHandler(handler)
    handler.close()


@contextmanager
def daily_log_session(category: str, cfg: Optional[Config] = None) -> Iterator[str]:
    """单次任务上下文：把该类别相关 logger 同步写入当日操作日志。

    用法::

        with daily_log_session("seed", cfg) as path:
            ...
    """
    _validate_category(category)
    cfg = cfg or load_config()
    path = daily_log_path(category, cfg)
    _maybe_prune_daily_logs(category, cfg)
    try:
        handler, loggers = _attach_daily_handler(category, cfg)
    except OSError:
        yield path
        return
    try:
        yield path
    finally:
        _detach_daily_handler(handler, loggers)
