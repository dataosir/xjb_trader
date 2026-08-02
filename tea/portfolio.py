"""资金 / 持仓 / 仓位计算（3/7 灰度建仓法）。

half_pos      = 资金 × max_position_pct × half_pos_mult
full_shares   = floor(half_pos / (买入价 × 100)) × 100
gray_shares   = min(floor(half_pos × 30% / (买入价 × 100)) × 100, full_shares)
confirm_shares= full_shares - gray_shares
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import utils
from .config_store import Config, load_config

STAGE_GRAY = "gray"
STAGE_FULL = "full"


def state_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("capital_state_file")


def empty_state() -> dict:
    return {"capital": 0.0, "positions": [], "updated_at": None, "history": []}


def load_state(cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    st = utils.read_json(state_path(cfg), default=None) or empty_state()
    st.setdefault("positions", [])
    st.setdefault("capital", 0.0)
    return st


def save_state(st: dict, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    st["updated_at"] = utils.now().strftime("%Y-%m-%d %H:%M:%S")
    return utils.write_json(state_path(cfg), st)


# ------------------------------------------------------------------ 资金

def get_capital(cfg: Optional[Config] = None) -> float:
    return float(load_state(cfg).get("capital") or 0.0)


def set_capital(amount: float, cfg: Optional[Config] = None) -> dict:
    st = load_state(cfg)
    st["capital"] = float(amount)
    save_state(st, cfg)
    return st


def position_cost(p: dict) -> float:
    return float(p.get("entry") or 0) * float(p.get("shares") or 0)


def used_cash(cfg: Optional[Config] = None) -> float:
    return sum(position_cost(p) for p in load_state(cfg).get("positions", []))


def available_cash(cfg: Optional[Config] = None) -> float:
    st = load_state(cfg)
    return float(st.get("capital") or 0.0) - sum(position_cost(p) for p in st.get("positions", []))


def positions(cfg: Optional[Config] = None) -> List[dict]:
    return load_state(cfg).get("positions", [])


def find_position(code: str, cfg: Optional[Config] = None) -> Optional[dict]:
    code = utils.norm_code(code)
    for p in positions(cfg):
        if p.get("code") == code:
            return p
    return None


def has_position(code: str, cfg: Optional[Config] = None) -> bool:
    return find_position(code, cfg) is not None


# ------------------------------------------------------------------ 9.1 仓位计算

def compute_position(capital: float, price: float, half_pos_mult: float = 1.0,
                     cfg: Optional[Config] = None, sl_pct: Optional[float] = None) -> dict:
    """3/7 灰度建仓：返回灰度/确认/总仓的股数与金额。"""
    cfg = cfg or load_config()
    lot = int(cfg.get("position.lot_size", 100))
    max_pct = float(cfg.s("max_position_pct", 0.50))
    gray_ratio = float(cfg.s("gray_ratio", 0.30))
    mult = utils.clamp(float(half_pos_mult or 1.0),
                       float(cfg.get("position.half_pos_mult_min", 0.25)),
                       float(cfg.get("position.half_pos_mult_max", 1.0)))
    half_pos = float(capital) * max_pct * mult
    unit = price * lot
    full_shares = utils.round_lot(half_pos / unit * lot, lot) if unit > 0 else 0
    gray_shares = min(utils.round_lot(half_pos * gray_ratio / unit * lot, lot) if unit > 0 else 0, full_shares)
    confirm_shares = max(0, full_shares - gray_shares)
    gray_amt = gray_shares * price
    full_amt = full_shares * price
    risk_amt = (full_amt * (sl_pct or 0) / 100.0) if sl_pct else None
    return {
        "capital": round(float(capital), 2),
        "half_pos_mult": round(mult, 4),
        "half_pos": round(half_pos, 2),
        "price": price,
        "full_shares": full_shares,
        "gray_shares": gray_shares,
        "confirm_shares": confirm_shares,
        "gray_amount": round(gray_amt, 2),
        "confirm_amount": round(confirm_shares * price, 2),
        "full_amount": round(full_amt, 2),
        "position_pct": round(full_amt / capital * 100.0, 2) if capital else None,
        "risk_amount": round(risk_amt, 2) if risk_amt else None,
        "risk_pct_of_capital": round(risk_amt / capital * 100.0, 2) if (risk_amt and capital) else None,
        "lot": lot,
        "enough": full_shares >= int(cfg.get("position.min_shares", 100)),
    }


def fees(amount: float, side: str = "buy", cfg: Optional[Config] = None) -> float:
    """佣金 + 印花税（卖出）估算。"""
    cfg = cfg or load_config()
    rate = float(cfg.get("position.fee_rate", 0.00025))
    minf = float(cfg.get("position.min_fee", 5.0))
    fee = max(amount * rate, minf)
    if side == "sell":
        fee += amount * float(cfg.get("position.stamp_tax", 0.0005))
    return round(fee, 2)


# ------------------------------------------------------------------ 持仓变更

def open_position(code: str, name: str, price: float, sizing: dict, ev: Optional[dict] = None,
                  cfg: Optional[Config] = None, gray_only: bool = True) -> dict:
    """记录灰度仓（默认只买 30% 灰度，确认后再补 70%）。"""
    cfg = cfg or load_config()
    st = load_state(cfg)
    shares = sizing["gray_shares"] if gray_only else sizing["full_shares"]
    lv = (ev or {}).get("levels") or {}
    pos = {
        "id": f"{utils.norm_code(code)}-{utils.stamp()}",
        "code": utils.norm_code(code), "name": name,
        "entry": price, "shares": shares,
        "gray_shares": sizing["gray_shares"], "confirm_shares": sizing["confirm_shares"],
        "full_shares": sizing["full_shares"],
        "stage": STAGE_GRAY if gray_only else STAGE_FULL,
        "sl_pct": lv.get("sl_pct"), "tp_pct": lv.get("tp_pct"),
        "stop": lv.get("stop"), "target": lv.get("target"), "odds": lv.get("odds"),
        "half_pos_mult": sizing.get("half_pos_mult"),
        "total_score": (ev or {}).get("total_score"),
        "identity_tier": ((ev or {}).get("identity") or {}).get("tier"),
        "stage_label": ((ev or {}).get("stage") or {}).get("stage"),
        "sector_name": ((ev or {}).get("sector") or {}).get("name"),
        "opened_at": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
        "opened_date": utils.today_str(),
        "cost": round(shares * price, 2),
        "fee": fees(shares * price, "buy", cfg),
    }
    st["positions"].append(pos)
    save_state(st, cfg)
    return pos


def add_confirm(code: str, price: float, cfg: Optional[Config] = None) -> Optional[dict]:
    """确认仓补足 70%。"""
    cfg = cfg or load_config()
    st = load_state(cfg)
    for p in st["positions"]:
        if p.get("code") == utils.norm_code(code) and p.get("stage") == STAGE_GRAY:
            add = int(p.get("confirm_shares") or 0)
            if add <= 0:
                return p
            total_cost = position_cost(p) + add * price
            p["shares"] = int(p.get("shares") or 0) + add
            p["entry"] = round(total_cost / p["shares"], 4) if p["shares"] else price
            p["stage"] = STAGE_FULL
            p["confirm_price"] = price
            p["confirmed_at"] = utils.now().strftime("%Y-%m-%d %H:%M:%S")
            p["cost"] = round(total_cost, 2)
            save_state(st, cfg)
            return p
    return None


def remove_position(code: str, cfg: Optional[Config] = None) -> Optional[dict]:
    cfg = cfg or load_config()
    st = load_state(cfg)
    code = utils.norm_code(code)
    for i, p in enumerate(st["positions"]):
        if p.get("code") == code:
            st["positions"].pop(i)
            save_state(st, cfg)
            return p
    return None


def format_positions(cfg: Optional[Config] = None) -> str:
    st = load_state(cfg)
    ps = st.get("positions", [])
    lines = [f"===== 资金/持仓（总资金 {utils.money(st.get('capital'))}，"
             f"可用 {utils.money(available_cash(cfg))}）====="]
    if not ps:
        lines.append("  当前空仓")
        return "\n".join(lines)
    for p in ps:
        lines.append(f"  {p['code']} {p.get('name')} {p.get('shares')} 股 @ {utils.num(p.get('entry'))} "
                     f"[{p.get('stage')}] 止损 {utils.pct(p.get('sl_pct'))} 止盈 {utils.pct(p.get('tp_pct'))} "
                     f"开仓 {p.get('opened_date')}")
    return "\n".join(lines)


def format_sizing(s: Dict[str, Any]) -> str:
    return ("仓位计算：半仓上限 {hp}（乘数 {m}）→ 总仓 {fs} 股 / {fa}\n"
            "  灰度 30%：{gs} 股 / {ga}   确认 70%：{cs} 股 / {ca}"
            + ("\n  单笔风险 {ra}（占总资金 {rp}%）" if s.get("risk_amount") else "")).format(
        hp=utils.money(s.get("half_pos")), m=s.get("half_pos_mult"),
        fs=s.get("full_shares"), fa=utils.money(s.get("full_amount")),
        gs=s.get("gray_shares"), ga=utils.money(s.get("gray_amount")),
        cs=s.get("confirm_shares"), ca=utils.money(s.get("confirm_amount")),
        ra=utils.money(s.get("risk_amount")), rp=s.get("risk_pct_of_capital"))
