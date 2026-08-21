"""种子标的每日价格跟踪：进入过种子文档的票，一路记到卖出/删除为止。

复盘「选了之后该拿几天」只靠 T+1~T+5 看不到完整轨迹。这里给每只进入过种子
文档的票记下每天收盘价，直到该票被卖出或从计划/持仓里删除。数据落在
price_track.json，供后续迭代（最优持有周期、止盈时机）做统计。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.data import Market

STATUS_TRACKING = "tracking"
STATUS_SOLD = "sold"
STATUS_REMOVED = "removed"


def track_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("price_track_file")


def load(cfg: Optional[Config] = None) -> Dict[str, dict]:
    return utils.read_json(track_path(cfg), default={})


def save(reg: Dict[str, dict], cfg: Optional[Config] = None) -> str:
    return utils.write_json(track_path(cfg), reg)


def ensure_tracked(codes: List[str], names: Dict[str, str],
                   cfg: Optional[Config] = None) -> int:
    """把新进入种子文档的票加入跟踪（已跟踪/已结束的不动），返回新增数。"""
    cfg = cfg or load_config()
    reg = load(cfg)
    today = utils.today_str()
    added = 0
    for code in codes:
        code = utils.norm_code(code)
        if not code or code in reg:
            continue
        reg[code] = {
            "code": code, "name": names.get(code) or code,
            "first_seen": today, "status": STATUS_TRACKING, "prices": [],
        }
        added += 1
    if added:
        save(reg, cfg)
    return added


def record_daily(market: Optional[Market] = None, cfg: Optional[Config] = None,
                 date: Optional[str] = None) -> dict:
    """给所有「跟踪中」的票记下当日价格（幂等：同一日期只记一次）。"""
    cfg = cfg or load_config()
    mk = market or Market(cfg)
    reg = load(cfg)
    d = date or utils.today_str()
    recorded = skipped = failed = 0
    changed = False
    for code, e in reg.items():
        if e.get("status") != STATUS_TRACKING:
            continue
        if any(p.get("date") == d for p in e.get("prices", [])):
            skipped += 1
            continue
        try:
            price = (mk.get_quote(code) or {}).get("price")
        except Exception:
            price = None
        if price is None:
            failed += 1
            continue
        e.setdefault("prices", []).append({"date": d, "price": round(float(price), 4)})
        recorded += 1
        changed = True
    if changed:
        save(reg, cfg)
    return {
        "recorded": recorded, "skipped": skipped, "failed": failed,
        "tracking": sum(1 for e in reg.values() if e.get("status") == STATUS_TRACKING),
    }


def mark_sold(code: str, cfg: Optional[Config] = None) -> None:
    _end(code, STATUS_SOLD, cfg)


def mark_removed(code: str, cfg: Optional[Config] = None) -> None:
    _end(code, STATUS_REMOVED, cfg)


def _end(code: str, status: str, cfg: Optional[Config] = None) -> None:
    cfg = cfg or load_config()
    reg = load(cfg)
    code = utils.norm_code(code)
    e = reg.get(code)
    if e and e.get("status") == STATUS_TRACKING:
        e["status"] = status
        e["ended_at"] = utils.today_str()
        save(reg, cfg)


def tracking_codes(cfg: Optional[Config] = None) -> List[str]:
    return [c for c, e in load(cfg).items() if e.get("status") == STATUS_TRACKING]
