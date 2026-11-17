"""交易时间窗口 selftest：周五谨慎提示。"""
from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING

from tea.core.timing import Timing

if TYPE_CHECKING:
    from tea.config.config_store import Config
    from tea.selftest import Suite


def check_friday_caution(t: Suite, cfg: Config) -> None:
    """周五买入窗口保持开启，仅提示尽量不开新仓；周一无提示。"""
    t.head("交易窗口 · 周五谨慎")
    old = cfg.get("timing.friday_caution", True)
    try:
        cfg.set("timing.friday_caution", True)
        tm = Timing(cfg)
        fri = _dt.datetime(2026, 11, 13, 14, 10)
        mon = _dt.datetime(2026, 11, 16, 14, 10)
        t.eq("周五买入窗口仍开启", tm.is_buy_window(fri), True)
        t.eq("周五阶段提示谨慎", tm.phase(fri), "买入窗口(14:00-14:45·周五谨慎)")
        t.eq("周五窗口文案含谨慎", tm.buy_window_text(fri), "14:00–14:45（周五谨慎，尽量不开新仓）")
        t.eq("周一买入窗口正常", tm.is_buy_window(mon), True)
        t.eq("周一阶段为买入窗口", tm.phase(mon), "买入窗口(14:00-14:45)")
        t.eq("周一窗口文案无提示", tm.buy_window_text(mon), "14:00–14:45")

        cfg.set("timing.friday_caution", False)
        tm2 = Timing(cfg)
        t.eq("关闭提示后周五文案无提示", tm2.buy_window_text(fri), "14:00–14:45")
    finally:
        cfg.set("timing.friday_caution", old)
