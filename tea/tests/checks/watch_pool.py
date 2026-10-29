"""观察池 selftest（轨道资格复检 / 自动剔除）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from tea.core import utils
from tea.portfolio import watch_pool as wp
from tea.screening import preflight

if TYPE_CHECKING:
    from tea.config.config_store import Config
    from tea.selftest import Suite


def check_watch_track_prune(t: Suite, cfg: Config) -> None:
    t.head("观察池 · 观察轨资格失效自动剔除")

    pool_path = cfg.data_file("watch_pool_file")
    item = {
        "code": "600100", "name": "资格票", "track": wp.TRACK_WATCH,
        "status": wp.STATUS_ACTIVE, "added_date": utils.today_str(),
        "ref_price": 10.0, "keep_days": 3, "identity_tier": "跟风",
    }
    utils.write_json(pool_path, {"items": [item], "removed": []})

    ev_soft = {
        "verdict": preflight.VERDICT_WATCH,
        "gap": 0,
        "veto": {"watchable": True, "soft": [{"name": "x"}], "rejected": False, "hard": []},
    }
    ev_near = {
        "verdict": preflight.VERDICT_NEAR_MISS,
        "gap": 3,
        "total_score": 3,
        "pass_threshold": 6,
        "veto": {"watchable": False, "soft": [], "rejected": False, "hard": []},
    }
    ev_pass = {
        "verdict": preflight.VERDICT_PASS,
        "gap": 0,
        "veto": {"watchable": False, "soft": [], "rejected": False, "hard": []},
    }

    t.ok("软否决仍保留", wp.ineligible_reason(item, ev_soft, cfg) is None)
    t.ok("近失应剔除", wp.ineligible_reason(item, ev_near, cfg) is not None)
    t.ok("PASS 不因无软否决剔除", wp.ineligible_reason(item, ev_pass, cfg) is None)

    orig = preflight.evaluate

    def _eval_near(code, market, c, **kw):
        return ev_near

    preflight.evaluate = _eval_near
    try:
        res = wp.prune_ineligible(market=None, cfg=cfg, apply=True)
        t.eq("近失自动剔除 1 只", len(res.get("removed") or []), 1)
        t.eq("池内已空", len(wp.items(cfg)), 0)
    finally:
        preflight.evaluate = orig

    pending = {
        "code": "600101", "name": "待定票", "track": wp.TRACK_PENDING,
        "status": wp.STATUS_ACTIVE, "added_date": utils.today_str(),
        "ref_price": 9.0, "keep_days": 3,
    }
    utils.write_json(pool_path, {"items": [pending], "removed": []})
    ev_gap2 = {
        "verdict": preflight.VERDICT_NEAR_MISS,
        "gap": 2,
        "veto": {"watchable": False, "rejected": False, "hard": [], "soft": []},
    }
    t.ok("启动待定轨 gap>1 应剔除", wp.ineligible_reason(pending, ev_gap2, cfg) is not None)
