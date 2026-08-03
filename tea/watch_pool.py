"""观察池管理：纳入 / 复核剔除 / 回踩就绪检测。

来源轨道：趋势轨（热点扫描）、观察轨（种子软否决）、启动待定轨（预审差1分）、
萌芽观察轨、前夕观察轨。上限 12 只，趋势轨保留 2 天、确认仓 3 天。
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from . import preflight, utils
from .config_store import Config, load_config
from .identity import TIER_ZAMAO
from .data import Market

TRACK_TREND = "趋势轨"
TRACK_WATCH = "观察轨"
TRACK_PENDING = "启动待定轨"
TRACK_SPROUT = "萌芽观察轨"
TRACK_EVE = "前夕观察轨"

# 已知轨道全集（与 config 默认 watch.tracks 一致）：入池/算保留天数前均校验，
# 不在集内的值不静默兜底（旧行为会把拼错的轨道名当回踩轨处理）。
KNOWN_TRACKS = (TRACK_TREND, TRACK_WATCH, TRACK_PENDING, TRACK_SPROUT, TRACK_EVE)

STATUS_ACTIVE = "active"
STATUS_READY = "ready"
STATUS_REMOVED = "removed"


def pool_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("watch_pool_file")


def load_pool(cfg: Optional[Config] = None) -> dict:
    data = utils.read_json(pool_path(cfg), default=None) or {"items": [], "removed": []}
    data.setdefault("items", [])
    data.setdefault("removed", [])
    # 历史数据里可能残留已下线/拼错的轨道名，跳过并告警，不让它们走到
    # keep_days_for() 里抛异常把整个观察池流程炸掉。
    bad = [i for i in data["items"] if i.get("track") not in KNOWN_TRACKS]
    if bad:
        data["items"] = [i for i in data["items"] if i.get("track") in KNOWN_TRACKS]
        print(f"[watch_pool] 忽略 {len(bad)} 条未知轨道记录 in {pool_path(cfg)}"
              f"（" + "、".join(f"{i.get('code')}:{i.get('track')}" for i in bad[:10])
              + (" ..." if len(bad) > 10 else "") + "）", file=sys.stderr)
    return data


def save_pool(pool: dict, cfg: Optional[Config] = None) -> str:
    pool["updated_at"] = utils.now().strftime("%Y-%m-%d %H:%M:%S")
    return utils.write_json(pool_path(cfg), pool)


def keep_days_for(track: str, cfg: Optional[Config] = None) -> int:
    """按轨道取保留天数。未知轨道直接 raise，不静默当回踩轨处理。"""
    cfg = cfg or load_config()
    if track == TRACK_TREND:
        return int(cfg.get("watch.trend_keep_days", 2))
    if track == TRACK_PENDING:
        return int(cfg.get("watch.confirm_keep_days", 3))
    if track in (TRACK_WATCH, TRACK_SPROUT, TRACK_EVE):
        return int(cfg.get("watch.pullback_keep_days", 3))
    raise ValueError(f"Unknown track type: {track!r}（已知轨道：{'、'.join(KNOWN_TRACKS)}）")


# ------------------------------------------------------------------ 纳入

def add(ev: dict, track: str = TRACK_WATCH, source: str = "", triggers: Optional[List[str]] = None,
        cfg: Optional[Config] = None, note: str = "") -> dict:
    """纳入观察池（同代码去重更新；超上限剔除最旧的低优先项）。"""
    cfg = cfg or load_config()
    if track not in KNOWN_TRACKS:
        raise ValueError(f"Unknown track type: {track!r}（已知轨道：{'、'.join(KNOWN_TRACKS)}）")
    pool = load_pool(cfg)
    snap = preflight.snapshot(ev)
    item = {
        "code": ev.get("code"), "name": ev.get("name"), "track": track, "source": source,
        "added_date": utils.today_str(), "added_at": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ref_price": (ev.get("quote") or {}).get("price"),
        "ref_high": (ev.get("quote") or {}).get("high"),
        "triggers": triggers or [], "note": note,
        "keep_days": keep_days_for(track, cfg),
        "total_score": ev.get("total_score"), "pass_threshold": ev.get("pass_threshold"),
        "gap": ev.get("gap"), "identity_tier": (ev.get("identity") or {}).get("tier"),
        "stage": (ev.get("stage") or {}).get("stage"),
        "sector_name": (ev.get("sector") or {}).get("name"),
        "snapshot": snap, "status": STATUS_ACTIVE,
    }
    items = [i for i in pool["items"] if i.get("code") != item["code"]]
    items.append(item)

    max_size = int(cfg.get("watch.max_size", 12))
    if len(items) > max_size:
        # 优先剔除：分差大 → 加入早
        items.sort(key=lambda i: (-(i.get("gap") or 0), i.get("added_at") or ""))
        dropped = items[:len(items) - max_size]
        for d in dropped:
            d["status"] = STATUS_REMOVED
            d["removed_reason"] = f"观察池超上限 {max_size}"
            d["removed_date"] = utils.today_str()
            pool["removed"].append(d)
        items = items[len(items) - max_size:]
    items.sort(key=lambda i: (i.get("gap") if i.get("gap") is not None else 9, i.get("added_at") or ""))
    pool["items"] = items
    save_pool(pool, cfg)
    return item


def remove(code: str, reason: str = "手动剔除", cfg: Optional[Config] = None) -> Optional[dict]:
    cfg = cfg or load_config()
    pool = load_pool(cfg)
    code = utils.norm_code(code)
    keep, gone = [], None
    for i in pool["items"]:
        if i.get("code") == code and gone is None:
            i["status"] = STATUS_REMOVED
            i["removed_reason"] = reason
            i["removed_date"] = utils.today_str()
            gone = i
            pool["removed"].append(i)
        else:
            keep.append(i)
    pool["items"] = keep
    save_pool(pool, cfg)
    return gone


def items(cfg: Optional[Config] = None, track: Optional[str] = None) -> List[dict]:
    its = load_pool(cfg).get("items", [])
    return [i for i in its if (track is None or i.get("track") == track)]


def find(code: str, cfg: Optional[Config] = None) -> Optional[dict]:
    code = utils.norm_code(code)
    return next((i for i in items(cfg) if i.get("code") == code), None)


# ------------------------------------------------------------------ 12.3 回踩就绪

def pullback_ready(item: dict, ev: dict, cfg: Optional[Config] = None) -> dict:
    """回踩就绪：最小回撤 ≥3% + 分时位置 ≤65% + 保留 ≤3 天。"""
    cfg = cfg or load_config()
    min_drop = float(cfg.get("watch.pullback_min_drop_pct", 3.0))
    max_intr = float(cfg.get("watch.pullback_max_intraday", 0.65))
    max_days = int(cfg.get("watch.pullback_keep_days", 3))
    ref = item.get("ref_high") or item.get("ref_price")
    price = (ev.get("quote") or {}).get("price")
    intr = ev.get("intraday")
    days = utils.days_between(item.get("added_date") or "", utils.today_str())
    drop = ((ref - price) / ref * 100.0) if (ref and price) else None
    checks = {
        "drop_ok": bool(drop is not None and drop >= min_drop),
        "intraday_ok": bool(intr is not None and intr <= max_intr),
        "fresh": bool(days is not None and days <= max_days),
        "drop": round(drop, 2) if drop is not None else None,
        "intraday": intr, "days": days,
    }
    checks["ready"] = all([checks["drop_ok"], checks["intraday_ok"], checks["fresh"]])
    checks["hint"] = ("回踩就绪，可预审" if checks["ready"] else
                      f"未就绪（回撤 {utils.pct(checks['drop'])}/需≥{min_drop:.0f}%，"
                      f"分时 {('%.0f%%' % (intr * 100)) if intr is not None else '—'}/需≤{max_intr:.0%}，"
                      f"持有 {days} 天/限 {max_days} 天）")
    return checks


# ------------------------------------------------------------------ 12.2 收盘复核

def review(market: Optional[Market] = None, cfg: Optional[Config] = None,
           sent: Optional[dict] = None, apply: bool = True) -> dict:
    """收盘复核：超时 / 身份降级 / VETO 恶化 → 剔除；顺带检测回踩就绪。"""
    cfg = cfg or load_config()
    pool = load_pool(cfg)
    mk = market or Market(cfg)
    out: Dict[str, Any] = {"checked": [], "removed": [], "ready": [],
                           "at": utils.now().strftime("%Y-%m-%d %H:%M")}

    for item in list(pool.get("items", [])):
        rec: Dict[str, Any] = {"code": item.get("code"), "name": item.get("name"),
                               "track": item.get("track"), "reasons": []}
        days = utils.days_between(item.get("added_date") or "", utils.today_str())
        keep = int(item.get("keep_days") or keep_days_for(item.get("track"), cfg))
        if days is not None and days > keep:
            rec["reasons"].append(f"超时（持有 {days} 天 > {keep} 天）")

        ev = None
        try:
            ev = preflight.evaluate(item["code"], mk, cfg, sent=sent)
        except Exception as exc:
            rec["reasons"].append(f"行情异常：{exc}")

        if ev:
            tier = (ev.get("identity") or {}).get("tier")
            if tier == TIER_ZAMAO and item.get("identity_tier") != TIER_ZAMAO:
                rec["reasons"].append(f"身份降级 {item.get('identity_tier')} → 杂毛")
            vt = ev.get("veto") or {}
            if vt.get("rejected"):
                rec["reasons"].append("VETO 恶化：" + "；".join(i["label"] for i in vt["hard"]))
            pb = pullback_ready(item, ev, cfg)
            rec["pullback"] = pb
            rec["now"] = preflight.snapshot(ev)
            if pb["ready"] and not rec["reasons"]:
                rec["ready"] = True
                out["ready"].append(rec)
                if apply:
                    item["status"] = STATUS_READY
                    item["ready_at"] = utils.now().strftime("%Y-%m-%d %H:%M")

        out["checked"].append(rec)
        if rec["reasons"] and apply and cfg.get("watch.auto_prune_on_review", True):
            remove(item["code"], "；".join(rec["reasons"]), cfg)
            out["removed"].append(rec)
    if apply:
        # 重新读取以持久化 ready 标记
        cur = load_pool(cfg)
        ready_codes = {r["code"] for r in out["ready"]}
        for i in cur["items"]:
            if i.get("code") in ready_codes:
                i["status"] = STATUS_READY
                i["ready_at"] = utils.now().strftime("%Y-%m-%d %H:%M")
        save_pool(cur, cfg)
    return out


def prune_expired(cfg: Optional[Config] = None) -> List[dict]:
    """仅按时效剔除（无需行情）。"""
    cfg = cfg or load_config()
    dropped = []
    for item in list(items(cfg)):
        days = utils.days_between(item.get("added_date") or "", utils.today_str())
        keep = int(item.get("keep_days") or keep_days_for(item.get("track"), cfg))
        if days is not None and days > keep:
            dropped.append(remove(item["code"], f"超时 {days}>{keep} 天", cfg))
    return [d for d in dropped if d]


# ------------------------------------------------------------------ 展示

def format_pool(cfg: Optional[Config] = None) -> str:
    its = items(cfg)
    max_size = int((cfg or load_config()).get("watch.max_size", 12))
    lines = [f"===== 观察池（{len(its)}/{max_size}）====="]
    if not its:
        lines.append("  空")
        return "\n".join(lines)
    for i in its:
        lines.append(f"  [{i.get('track')}] {i.get('code')} {i.get('name')} "
                     f"入池 {i.get('added_date')} 参考 {utils.num(i.get('ref_price'))} "
                     f"共振 {i.get('total_score')}/{i.get('pass_threshold')} "
                     f"{i.get('identity_tier')} {i.get('status')}")
        if i.get("triggers"):
            lines.append("      触发条件：" + "；".join(i["triggers"]))
    return "\n".join(lines)


def format_review(res: dict) -> str:
    lines = [f"===== 观察池复核 {res.get('at')} ====="]
    for r in res.get("checked", []):
        tag = "剔除" if r.get("reasons") else ("就绪" if r.get("ready") else "保留")
        lines.append(f"  [{tag}] {r['code']} {r.get('name')}"
                     + (f" — {'；'.join(r['reasons'])}" if r.get("reasons") else ""))
        if r.get("pullback") and not r.get("reasons"):
            lines.append(f"      {r['pullback']['hint']}")
    if not res.get("checked"):
        lines.append("  观察池为空")
    return "\n".join(lines)
