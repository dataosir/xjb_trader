"""交易时间窗口判定（法：什么时候能做什么）。"""
from __future__ import annotations

import datetime as _dt
from typing import Optional, Tuple

from . import utils
from .config_store import Config, load_config


def _hm(s: str) -> Tuple[int, int]:
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:
        return 0, 0


def to_minutes(s: str) -> int:
    h, m = _hm(s)
    return h * 60 + m


def now_minutes(when: Optional[_dt.datetime] = None) -> int:
    t = when or utils.now()
    return t.hour * 60 + t.minute


def in_window(start: str, end: str, when: Optional[_dt.datetime] = None, tol: int = 0) -> bool:
    cur = now_minutes(when)
    return (to_minutes(start) - tol) <= cur <= (to_minutes(end) + tol)


class Timing:
    def __init__(self, cfg: Optional[Config] = None):
        self.cfg = cfg or load_config()
        self.tol = int(self.cfg.get("timing.window_tolerance_min", 0))

    # -------------------------------------------------- 基础
    def is_trading_day(self, when: Optional[_dt.datetime] = None) -> bool:
        if self.cfg.get("timing.allow_weekend_ops", False):
            return True
        return utils.is_trading_day((when or utils.now()).date())

    def in_session(self, when: Optional[_dt.datetime] = None) -> bool:
        if not self.is_trading_day(when):
            return False
        return (in_window(self.cfg.get("timing.session_am_start"), self.cfg.get("timing.session_am_end"), when)
                or in_window(self.cfg.get("timing.session_pm_start"), self.cfg.get("timing.session_pm_end"), when))

    # -------------------------------------------------- 关键窗口
    def is_buy_window(self, when: Optional[_dt.datetime] = None) -> bool:
        """唯一新开买入窗口 14:00–14:45。"""
        return self.is_trading_day(when) and in_window(
            self.cfg.get("timing.buy_window_start"), self.cfg.get("timing.buy_window_end"), when, self.tol)

    def is_seed_window(self, when: Optional[_dt.datetime] = None, slack: int = 20) -> bool:
        """种子扫描窗口（14:30 前后 slack 分钟）。"""
        cur = now_minutes(when)
        t = to_minutes(self.cfg.get("timing.seed_scan"))
        return self.is_trading_day(when) and (t - slack) <= cur <= (t + slack + 15)

    def is_plan_recheck_window(self, when: Optional[_dt.datetime] = None, slack: int = 15) -> bool:
        cur = now_minutes(when)
        t = to_minutes(self.cfg.get("timing.plan_recheck"))
        return abs(cur - t) <= slack

    def is_after_close(self, when: Optional[_dt.datetime] = None) -> bool:
        return now_minutes(when) >= to_minutes(self.cfg.get("timing.session_pm_end"))

    def is_overnight_review_window(self, when: Optional[_dt.datetime] = None, slack: int = 30) -> bool:
        cur = now_minutes(when)
        return abs(cur - to_minutes(self.cfg.get("timing.overnight_review"))) <= slack

    # -------------------------------------------------- 描述
    def phase(self, when: Optional[_dt.datetime] = None) -> str:
        t = when or utils.now()
        if not self.is_trading_day(t):
            return "非交易日"
        cur = now_minutes(t)
        if cur < to_minutes(self.cfg.get("timing.session_am_start")):
            return "盘前"
        if self.is_overnight_review_window(t):
            return "隔夜复核(10:00)"
        if self.is_buy_window(t):
            return "买入窗口(14:00-14:45)"
        if self.is_seed_window(t):
            return "种子扫描(14:30)"
        if self.in_session(t):
            return "盘中"
        return "盘后"

    def describe(self, when: Optional[_dt.datetime] = None) -> str:
        t = when or utils.now()
        return (f"{t.strftime('%Y-%m-%d %H:%M')} | {self.phase(t)} | "
                f"买入窗口={'开' if self.is_buy_window(t) else '关'}")

    def buy_window_text(self) -> str:
        return f"{self.cfg.get('timing.buy_window_start')}–{self.cfg.get('timing.buy_window_end')}"
