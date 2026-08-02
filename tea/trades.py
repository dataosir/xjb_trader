"""平仓 / 撤销 / 交易流水（连亏冷却与历史胜率的数据源）。"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import portfolio, utils
from .config_store import Config, load_config

RESULT_WIN = "win"
RESULT_LOSS = "loss"
RESULT_FLAT = "flat"


def trades_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("trades_file")


def load_trades(cfg: Optional[Config] = None) -> List[dict]:
    data = utils.read_json(trades_path(cfg), default=None) or {}
    return data.get("trades", [])


def save_trades(trades: List[dict], cfg: Optional[Config] = None) -> str:
    return utils.write_json(trades_path(cfg), {
        "updated_at": utils.now().strftime("%Y-%m-%d %H:%M:%S"), "trades": trades})


# ------------------------------------------------------------------ 平仓

def close_position(code: str, exit_price: float, reason: str = "手动平仓",
                   cfg: Optional[Config] = None, shares: Optional[int] = None) -> Optional[dict]:
    """平仓：写入流水、回收资金（capital += 盈亏）、移除持仓。"""
    cfg = cfg or load_config()
    pos = portfolio.find_position(code, cfg)
    if not pos:
        return None
    qty = int(shares or pos.get("shares") or 0)
    entry = float(pos.get("entry") or 0)
    gross = (exit_price - entry) * qty
    fee = portfolio.fees(entry * qty, "buy", cfg) + portfolio.fees(exit_price * qty, "sell", cfg)
    pnl = gross - fee
    pnl_pct = ((exit_price - entry) / entry * 100.0) if entry else None
    sl_pct = pos.get("sl_pct")
    r_mult = (pnl_pct / sl_pct) if (pnl_pct is not None and sl_pct) else None
    rec = {
        "id": pos.get("id"), "code": pos.get("code"), "name": pos.get("name"),
        "entry": entry, "exit": exit_price, "shares": qty,
        "sl_pct": sl_pct, "tp_pct": pos.get("tp_pct"), "odds": pos.get("odds"),
        "total_score": pos.get("total_score"), "identity_tier": pos.get("identity_tier"),
        "stage_label": pos.get("stage_label"), "sector_name": pos.get("sector_name"),
        "half_pos_mult": pos.get("half_pos_mult"),
        "opened_date": pos.get("opened_date"), "opened_at": pos.get("opened_at"),
        "closed_date": utils.today_str(), "closed_at": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hold_days": utils.days_between(pos.get("opened_date") or "", utils.today_str()),
        "fee": round(fee, 2), "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "r_multiple": round(r_mult, 2) if r_mult is not None else None,
        "result": RESULT_WIN if pnl > 0 else (RESULT_LOSS if pnl < 0 else RESULT_FLAT),
        "reason": reason,
    }
    trades = load_trades(cfg)
    trades.append(rec)
    save_trades(trades, cfg)

    st = portfolio.load_state(cfg)
    st["capital"] = float(st.get("capital") or 0.0) + pnl
    st["positions"] = [p for p in st.get("positions", []) if p.get("code") != utils.norm_code(code)]
    portfolio.save_state(st, cfg)
    return rec


def cancel_position(code: str, reason: str = "误开仓撤销", cfg: Optional[Config] = None) -> Optional[dict]:
    """撤销开仓（当日误操作）：退回资金，不计入交易统计。"""
    cfg = cfg or load_config()
    pos = portfolio.remove_position(code, cfg)
    if not pos:
        return None
    trades = load_trades(cfg)
    trades.append({
        "id": pos.get("id"), "code": pos.get("code"), "name": pos.get("name"),
        "entry": pos.get("entry"), "exit": pos.get("entry"), "shares": pos.get("shares"),
        "opened_date": pos.get("opened_date"), "closed_date": utils.today_str(),
        "pnl": 0.0, "pnl_pct": 0.0, "result": "cancelled", "reason": reason,
        "total_score": pos.get("total_score"), "identity_tier": pos.get("identity_tier"),
    })
    save_trades(trades, cfg)
    return pos


# ------------------------------------------------------------------ 统计辅助

def effective_trades(cfg: Optional[Config] = None) -> List[dict]:
    """仅统计真实平仓（排除撤销）。"""
    return [t for t in load_trades(cfg) if t.get("result") in (RESULT_WIN, RESULT_LOSS, RESULT_FLAT)]


def consec_losses(cfg: Optional[Config] = None) -> int:
    """尾部连续亏损笔数（连亏冷却用）。"""
    n = 0
    for t in reversed(effective_trades(cfg)):
        if t.get("result") == RESULT_LOSS:
            n += 1
        else:
            break
    return n


def last_trade(cfg: Optional[Config] = None) -> Optional[dict]:
    ts = effective_trades(cfg)
    return ts[-1] if ts else None


def summary(cfg: Optional[Config] = None) -> Dict[str, object]:
    ts = effective_trades(cfg)
    wins = [t for t in ts if t.get("result") == RESULT_WIN]
    losses = [t for t in ts if t.get("result") == RESULT_LOSS]
    pnl = sum(float(t.get("pnl") or 0) for t in ts)
    avg_win = utils.mean([float(t.get("pnl") or 0) for t in wins])
    avg_loss = utils.mean([abs(float(t.get("pnl") or 0)) for t in losses])
    return {
        "n": len(ts), "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / len(ts)) if ts else None,
        "pnl": round(pnl, 2),
        "avg_win": round(avg_win, 2) if avg_win else None,
        "avg_loss": round(avg_loss, 2) if avg_loss else None,
        "profit_factor": round((avg_win or 0) / avg_loss, 2) if avg_loss else None,
        "avg_r": utils.mean([float(t["r_multiple"]) for t in ts if t.get("r_multiple") is not None]),
        "consec_losses": consec_losses(cfg),
    }


def format_trades(cfg: Optional[Config] = None, limit: int = 10) -> str:
    ts = load_trades(cfg)[-limit:]
    s = summary(cfg)
    lines = [f"===== 交易流水（共 {s['n']} 笔，胜率 "
             f"{('%.1f%%' % (s['win_rate'] * 100)) if s['win_rate'] is not None else '—'}，"
             f"累计盈亏 {utils.money(s['pnl'])}，连亏 {s['consec_losses']}）====="]
    for t in ts:
        lines.append(f"  {t.get('closed_date')} {t.get('code')} {t.get('name')} "
                     f"{utils.num(t.get('entry'))}→{utils.num(t.get('exit'))} "
                     f"{utils.pct(t.get('pnl_pct'))} R {utils.num(t.get('r_multiple'))} "
                     f"[{t.get('result')}] {t.get('reason')}")
    if not ts:
        lines.append("  暂无记录")
    return "\n".join(lines)
