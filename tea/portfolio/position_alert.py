"""持仓卖点提醒：止损/止盈扫描与按日去重状态。

观察池提醒（watch_pool）管「买点」，本模块管「卖点」：逐只读持仓成本与
止损/止盈，盘中现价触线时返回待提醒列表。只发提醒，不自动平仓。
"""
from __future__ import annotations

from typing import Dict, Optional

from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.data import Market
from tea.portfolio import portfolio

CONDITION_STOP = "stop_loss"
CONDITION_TARGET = "take_profit"
CONDITION_LABELS = {
    CONDITION_STOP: "止损触发",
    CONDITION_TARGET: "止盈触发",
}


def state_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("position_alert_state_file")


def load_state(cfg: Optional[Config] = None) -> dict:
    data = utils.read_json(state_path(cfg), default=None) or {"sent": []}
    data.setdefault("sent", [])
    return data


def save_state(state: dict, cfg: Optional[Config] = None) -> str:
    state["updated_at"] = utils.now().strftime("%Y-%m-%d %H:%M:%S")
    return utils.write_json(state_path(cfg), state)


def _alert_dedupe_key(date: str, code: str, condition: str) -> str:
    return f"{date}|{utils.norm_code(code)}|{condition}"


def is_alert_sent(date: str, code: str, condition: str, cfg: Optional[Config] = None) -> bool:
    key = _alert_dedupe_key(date, code, condition)
    return any(s.get("key") == key for s in load_state(cfg).get("sent", []))


def mark_alert_sent(date: str, code: str, condition: str, cfg: Optional[Config] = None,
                    meta: Optional[dict] = None) -> None:
    state = load_state(cfg)
    key = _alert_dedupe_key(date, code, condition)
    if any(s.get("key") == key for s in state.get("sent", [])):
        return
    rec = {
        "key": key,
        "date": date,
        "code": utils.norm_code(code),
        "condition": condition,
        "sent_at": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if meta:
        rec.update(meta)
    state.setdefault("sent", []).append(rec)
    save_state(state, cfg)


def exit_levels(pos: dict, cfg: Optional[Config] = None) -> Dict[str, object]:
    """取持仓的止损/止盈价；缺失时按成本与百分点反推，止损再兜底默认值。"""
    cfg = cfg or load_config()
    code = str(pos.get("code") or "")
    entry = float(pos.get("entry") or 0.0)
    stop = pos.get("stop")
    target = pos.get("target")
    sl_pct = pos.get("sl_pct")
    tp_pct = pos.get("tp_pct")
    stop_basis = "持仓止损" if stop is not None else None
    target_basis = "持仓止盈" if target is not None else None

    if stop is None and sl_pct is not None and entry > 0:
        stop = utils.round_price_down(entry * (1.0 - float(sl_pct) / 100.0), code)
        stop_basis = f"成本×{float(sl_pct):g}%"
    if target is None and tp_pct is not None and entry > 0:
        target = utils.round_price_up(entry * (1.0 + float(tp_pct) / 100.0), code)
        target_basis = f"成本×{float(tp_pct):g}%"

    if stop is None and entry > 0 and cfg.get("alert.position_fallback_stop", True):
        fallback = float(cfg.get("position.fallback_sl_pct", 3.0))
        stop = utils.round_price_down(entry * (1.0 - fallback / 100.0), code)
        stop_basis = f"默认止损{fallback:g}%（未录止损）"

    return {
        "entry": entry,
        "stop": float(stop) if stop is not None else None,
        "target": float(target) if target is not None else None,
        "sl_pct": float(sl_pct) if sl_pct is not None else None,
        "tp_pct": float(tp_pct) if tp_pct is not None else None,
        "stop_basis": stop_basis if stop is not None else None,
        "target_basis": target_basis if target is not None else None,
    }


def format_alert_body(cand: dict) -> str:
    """邮件正文（卖点提醒；不自动卖出）。"""
    label = CONDITION_LABELS.get(cand.get("condition"), cand.get("condition") or "卖点")
    chg = cand.get("chg_pct")
    lines = [
        f"标的：{cand.get('code')} {cand.get('name')}",
        f"提醒条件：{label}",
        f"成本：{utils.num(cand.get('entry'))}　现价：{utils.num(cand.get('price'))}"
        + (f"（{utils.pct(chg)}）" if chg is not None else ""),
        f"持仓：{cand.get('shares')} 股　浮动盈亏：{utils.money(cand.get('pnl'))}"
        f"（{utils.pct(cand.get('pnl_pct'))}）"
        + (f"　R {utils.num(cand.get('r_multiple'))}"
           if cand.get("r_multiple") is not None else ""),
        f"止损：{utils.num(cand.get('stop'))}"
        + (f"（{cand.get('stop_basis')}）" if cand.get("stop_basis") else "")
        + f"　止盈：{utils.num(cand.get('target'))}"
        + (f"（{cand.get('target_basis')}）" if cand.get("target_basis") else ""),
    ]
    if cand.get("opened_date"):
        lines.append(f"建仓：{cand.get('opened_date')}　持有 {cand.get('hold_days')} 天")
    lines += [
        "",
        f"请手动执行 tea close {cand.get('code')} 复核；引擎不自动卖出。",
    ]
    return "\n".join(lines)


def scan_alerts(market: Optional[Market] = None, cfg: Optional[Config] = None) -> dict:
    """扫描当前持仓，返回触发了止损/止盈的待提醒列表（不发信）。"""
    cfg = cfg or load_config()
    mk = market or Market(cfg)
    poss = portfolio.positions(cfg)
    out: Dict[str, object] = {
        "scanned": len(poss),
        "candidates": [],
        "errors": [],
        "missing_levels": [],
        "at": utils.now().strftime("%Y-%m-%d %H:%M"),
    }

    for pos in poss:
        code = pos.get("code")
        name = pos.get("name")
        lv = exit_levels(pos, cfg)
        stop, target = lv.get("stop"), lv.get("target")
        if stop is None and target is None:
            out["missing_levels"].append({"code": code, "name": name})
            continue
        try:
            quote = mk.get_quote(code) or {}
        except Exception as exc:
            out["errors"].append({"code": code, "error": str(exc)})
            continue
        price = quote.get("price")
        if price is None or float(price) <= 0:
            out["errors"].append({"code": code, "error": "无现价"})
            continue
        price = float(price)

        # 同一根行情同时越线时按止损优先，与 followthrough._simulate_exit 保守口径一致。
        if stop is not None and price <= float(stop):
            condition = CONDITION_STOP
        elif target is not None and price >= float(target):
            condition = CONDITION_TARGET
        else:
            continue

        entry = float(lv.get("entry") or 0.0)
        shares = int(pos.get("shares") or 0)
        pnl = (price - entry) * shares if entry else None
        pnl_pct = (price / entry - 1.0) * 100.0 if entry else None
        sl_pct = lv.get("sl_pct")
        r_mult = (pnl_pct / float(sl_pct)) if (pnl_pct is not None and sl_pct) else None
        rec = {
            "code": code,
            "name": name,
            "condition": condition,
            "condition_label": CONDITION_LABELS[condition],
            "entry": entry,
            "price": price,
            "chg_pct": quote.get("chg_pct"),
            "shares": shares,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "r_multiple": round(r_mult, 2) if r_mult is not None else None,
            "stop": stop,
            "target": target,
            "stop_basis": lv.get("stop_basis"),
            "target_basis": lv.get("target_basis"),
            "sl_pct": sl_pct,
            "tp_pct": lv.get("tp_pct"),
            "opened_date": pos.get("opened_date"),
            "hold_days": utils.days_between(pos.get("opened_date") or "", utils.today_str()),
            "stage": pos.get("stage"),
        }
        rec["body"] = format_alert_body(rec)
        out["candidates"].append(rec)
    return out
