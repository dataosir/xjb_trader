"""运行日志：logs/ 独立目录 + 按日切割（标准库 logging，零第三方依赖）。

与 data/ 下的结构化追溯（accumulator / seed_trace / scan_details / seed_records）
不同，这里记的是「程序运行过程」：谁在何时、以什么参数、走了哪条分支、算出什么
结果。用于日后**根据日志迭代程序**，不直接参与选股决策。

- ``init_logging(cfg)``：进程启动时调用一次，把 root 日志落到 ``logs/tea.log``。
- ``get_logger(name)``：取 ``tea`` 命名空间下的 logger，直接 ``.info/.warning/.error``。
- 按日切割：``TimedRotatingFileHandler(when="midnight")``，保留 ``backupCount`` 天，
  切割后的历史文件形如 ``tea.log.2026-08-18``。

日志目录不可写时静默降级（程序照常跑，只是不落运行日志），结构化数据仍走
``data/`` 目录不受影响。
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from typing import Optional

from tea.config.config_store import Config, load_config

_LOGGER_NAME = "tea"
_initialized = False


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
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
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
