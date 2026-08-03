"""次日交易计划管理（法：计划绑定 —— 无计划不可买）。

trade_plan.json 状态机：pending → ready_exec / invalid / executed / cleared。
plan-check 对比"计划快照 vs 当前预审"，任一变动则计划作废。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tea.analysis.identity import TIER_ZAMAO
from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.data import Market
from tea.screening import preflight

STATUS_PENDING = "pending"
STATUS_READY = "ready_exec"
STATUS_INVALID = "invalid"
STATUS_EXECUTED = "executed"
STATUS_CLEARED = "cleared"

PLAN_VERSION = 1


def state_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("plan_file")


def empty_plan() -> dict:
    return {"version": PLAN_VERSION, "planned_date": None, "execute_date": None,
            "status": STATUS_CLEARED, "items": [], "notes": []}


def load_plan(cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    data = utils.read_json(state_path(cfg), default=None)
    if not data:
        return empty_plan()
    data.setdefault("items", [])
    data.setdefault("status", STATUS_PENDING)
    return data


def save_plan(plan: dict, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    plan["version"] = PLAN_VERSION
    plan["updated_at"] = utils.now().strftime("%Y-%m-%d %H:%M:%S")
    return utils.write_json(state_path(cfg), plan)


# ------------------------------------------------------------------ 写计划

def item_from_eval(ev: dict) -> dict:
    lv = ev.get("levels") or {}
    return {
        "code": ev.get("code"), "name": ev.get("name"),
        "ref_price": lv.get("entry") or (ev.get("quote") or {}).get("price"),
        "sl_pct": lv.get("sl_pct"), "tp_pct": lv.get("tp_pct"),
        "odds": lv.get("odds"),
        "pass_th": ev.get("pass_threshold"), "total_score": ev.get("total_score"),
        "identity_tier": (ev.get("identity") or {}).get("tier"),
        "identity_score": (ev.get("identity") or {}).get("score"),
        "stage": (ev.get("stage") or {}).get("stage"),
        "sector_name": (ev.get("sector") or {}).get("name"),
        "sector_rank": (ev.get("sector") or {}).get("rank"),
        "track": ev.get("track") or "可买",
        "tier": ev.get("tier_label"),
        "snapshot": preflight.snapshot(ev),
        "status": STATUS_PENDING,
    }


def write_plan(evaluations: List[dict], cfg: Optional[Config] = None,
               planned_date: Optional[str] = None, execute_date: Optional[str] = None,
               notes: Optional[List[str]] = None) -> dict:
    """写次日计划（T 日 14:30 生成，T+1 执行）。"""
    cfg = cfg or load_config()
    plan = {
        "version": PLAN_VERSION,
        "planned_date": planned_date or utils.today_str(),
        "execute_date": execute_date or utils.today_str(utils.next_trading_day()),
        "status": STATUS_PENDING,
        "items": [item_from_eval(ev) for ev in evaluations],
        "notes": notes or [],
    }
    save_plan(plan, cfg)
    return plan


# ------------------------------------------------------------------ 查询

def active_items(plan: dict) -> List[dict]:
    return [i for i in plan.get("items", []) if i.get("status") in (STATUS_PENDING, STATUS_READY)]


def is_valid_today(plan: dict, cfg: Optional[Config] = None) -> bool:
    """今日是否存在有效计划（执行日 = 今天，状态非 invalid/cleared，且有未执行标的）。"""
    if not plan or plan.get("status") in (STATUS_INVALID, STATUS_CLEARED):
        return False
    if plan.get("execute_date") != utils.today_str():
        return False
    return bool(active_items(plan))


def planned_codes(plan: dict, only_active: bool = True) -> List[str]:
    items = active_items(plan) if only_active else plan.get("items", [])
    return [i.get("code") for i in items if i.get("code")]


def find_item(plan: dict, code: str) -> Optional[dict]:
    code = utils.norm_code(code)
    for i in plan.get("items", []):
        if i.get("code") == code:
            return i
    return None


def in_plan_today(code: str, cfg: Optional[Config] = None) -> bool:
    cfg = cfg or load_config()
    plan = load_plan(cfg)
    if not is_valid_today(plan, cfg):
        return False
    return utils.norm_code(code) in planned_codes(plan)


# ------------------------------------------------------------------ 状态机

def mark_ready(cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    plan = load_plan(cfg)
    if plan.get("execute_date") == utils.today_str() and plan.get("status") == STATUS_PENDING:
        plan["status"] = STATUS_READY
        for i in plan["items"]:
            if i.get("status") == STATUS_PENDING:
                i["status"] = STATUS_READY
        save_plan(plan, cfg)
    return plan


def mark_executed(code: str, cfg: Optional[Config] = None, detail: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    plan = load_plan(cfg)
    item = find_item(plan, code)
    if item:
        item["status"] = STATUS_EXECUTED
        item["executed_at"] = utils.now().strftime("%Y-%m-%d %H:%M:%S")
        if detail:
            item["execution"] = detail
        if all(i.get("status") in (STATUS_EXECUTED, STATUS_INVALID) for i in plan["items"]):
            plan["status"] = STATUS_EXECUTED
        save_plan(plan, cfg)
    return plan


def invalidate(reason: str, cfg: Optional[Config] = None, codes: Optional[List[str]] = None) -> dict:
    """计划作废（默认整单作废，符合"有变动则当天不再操作"纪律）。"""
    cfg = cfg or load_config()
    plan = load_plan(cfg)
    plan["status"] = STATUS_INVALID
    plan.setdefault("notes", []).append(f"[{utils.now().strftime('%m-%d %H:%M')}] 作废：{reason}")
    for i in plan.get("items", []):
        if codes and i.get("code") not in codes:
            continue
        if i.get("status") in (STATUS_PENDING, STATUS_READY):
            i["status"] = STATUS_INVALID
            i["invalid_reason"] = reason
    save_plan(plan, cfg)
    return plan


def clear_plan(cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    plan = load_plan(cfg)
    plan["status"] = STATUS_CLEARED
    for i in plan.get("items", []):
        if i.get("status") in (STATUS_PENDING, STATUS_READY):
            i["status"] = STATUS_CLEARED
    save_plan(plan, cfg)
    return plan


# ------------------------------------------------------------------ 11.2 变动检测

def check_item(item: dict, ev: dict, cfg: Optional[Config] = None) -> List[dict]:
    """对比计划快照与当前预审，返回变动列表（空 = 无变动）。"""
    cfg = cfg or load_config()
    snap = item.get("snapshot") or {}
    changes: List[dict] = []

    def chg(kind: str, detail: str) -> None:
        changes.append({"kind": kind, "detail": detail})

    vt = ev.get("veto") or {}
    if vt.get("items"):
        chg("VETO", "出现否决：" + "；".join(i["label"] for i in vt["items"]))

    q = ev.get("quote") or {}
    ref = item.get("ref_price") or snap.get("price")
    price = q.get("price")
    dev_max = float(cfg.s("plan_max_price_dev_pct", 1.5))
    if ref and price:
        dev = (price - ref) / ref * 100.0
        if abs(dev) > dev_max:
            chg("价格偏离", f"现价 {price:.2f} vs 计划 {ref:.2f}（{dev:+.2f}% > ±{dev_max}%）")

    slip_max = int(cfg.s("plan_max_sector_rank_slip", 8))
    r0, r1 = snap.get("sector_rank"), (ev.get("sector") or {}).get("rank")
    if r0 and r1 and (r1 - r0) > slip_max:
        chg("板块下滑", f"板块排名 {r0} → {r1}（下滑 {r1 - r0} 名 > {slip_max}）")

    th = ev.get("pass_threshold")
    total = ev.get("total_score")
    if total is not None and th is not None and total < th:
        chg("共振分不足", f"共振分 {total} < 当前门槛 {th}")

    min_odds = float(cfg.s("min_odds", 3))
    odds = (ev.get("levels") or {}).get("odds")
    if odds is not None and odds < min_odds:
        chg("盈亏比不足", f"R:R {odds:.2f} < {min_odds:.0f}")

    tier = (ev.get("identity") or {}).get("tier")
    if tier == TIER_ZAMAO and snap.get("identity_tier") != TIER_ZAMAO:
        chg("身份降级", f"身份 {snap.get('identity_tier')} → 杂毛")

    b0, b1 = snap.get("bias_ma20"), (ev.get("ind") or {}).get("bias_ma20")
    bias_worse = float(cfg.get("scoring.bias_penalty_pct", 8.0))
    if b0 is not None and b1 is not None and (b1 - b0) > bias_worse:
        chg("乖离恶化", f"MA20 乖离 {b0:.2f}% → {b1:.2f}%（恶化 {b1 - b0:.2f}% > {bias_worse:.0f}%）")

    intr = ev.get("intraday")
    intr_max = float(cfg.get("scoring.intraday_overheat_pct", 0.85))
    if intr is not None and intr > intr_max:
        chg("分时恶化", f"分时位置 {intr:.0%} > {intr_max:.0%}")

    return changes


def check_plan(market: Optional[Market] = None, cfg: Optional[Config] = None,
               sent: Optional[dict] = None, apply: bool = True) -> dict:
    """计划复核：逐项对比，任一变动则整单作废。"""
    cfg = cfg or load_config()
    plan = load_plan(cfg)
    out: Dict[str, Any] = {"plan": plan, "results": [], "changed": False,
                           "status": plan.get("status"), "checked_at": utils.now().strftime("%Y-%m-%d %H:%M")}
    items = active_items(plan)
    if not items:
        out["message"] = "无待执行计划项"
        return out

    mk = market or Market(cfg)
    for item in items:
        try:
            ev = preflight.evaluate(item["code"], mk, cfg, sent=sent,
                                           sl_pct=item.get("sl_pct"), tp_pct=item.get("tp_pct"))
        except Exception as exc:
            out["results"].append({"code": item["code"], "name": item.get("name"),
                                   "error": str(exc), "changes": [{"kind": "行情异常", "detail": str(exc)}]})
            out["changed"] = True
            continue
        changes = check_item(item, ev, cfg)
        out["results"].append({
            "code": item["code"], "name": item.get("name"), "changes": changes,
            "now": preflight.snapshot(ev), "plan_snapshot": item.get("snapshot"),
        })
        if changes:
            out["changed"] = True

    if apply:
        if out["changed"]:
            reasons = "；".join(f"{r['code']}:{c['kind']}" for r in out["results"] for c in r.get("changes", []))
            plan = invalidate(reasons or "计划复核出现变动", cfg)
            out["status"] = STATUS_INVALID
        elif plan.get("execute_date") == utils.today_str():
            plan = mark_ready(cfg)
            out["status"] = plan.get("status")
        out["plan"] = plan
    return out


# ------------------------------------------------------------------ 展示

def format_plan(plan: dict) -> str:
    if not plan.get("items"):
        return "交易计划：空（无次日计划）"
    lines = [f"===== 交易计划 {plan.get('planned_date')} → 执行 {plan.get('execute_date')}"
             f"（状态 {plan.get('status')}）====="]
    for i in plan["items"]:
        lines.append(
            f"  {i.get('code')} {i.get('name')} 参考价 {utils.num(i.get('ref_price'))} "
            f"止损 {utils.pct(i.get('sl_pct'))} 止盈 {utils.pct(i.get('tp_pct'))} "
            f"R:R {utils.num(i.get('odds'))} 共振 {i.get('total_score')}/{i.get('pass_th')} "
            f"{i.get('identity_tier')} [{i.get('status')}]")
    for n in plan.get("notes", [])[-5:]:
        lines.append(f"  · {n}")
    return "\n".join(lines)


def format_check(res: dict) -> str:
    lines = [f"===== 计划复核 {res.get('checked_at')} ====="]
    if res.get("message"):
        lines.append(res["message"])
        return "\n".join(lines)
    for r in res.get("results", []):
        if r.get("changes"):
            lines.append(f"  {r['code']} {r.get('name')} 变动 {len(r['changes'])} 项：")
            for c in r["changes"]:
                lines.append(f"      - [{c['kind']}] {c['detail']}")
        else:
            now = r.get("now") or {}
            lines.append(f"  {r['code']} {r.get('name')} 无变动（现价 {utils.num(now.get('price'))}，"
                         f"共振 {now.get('total_score')}/{now.get('pass_threshold')}）")
    lines.append("→ 结论：" + ("计划作废，当天不再操作" if res.get("changed") else "计划有效，可在买入窗口执行"))
    return "\n".join(lines)
