"""数学期望 + 仓位调整（E[R] = p̂ × odds - (1 - p̂)）。

p̂ 优先取同评分段历史胜率（需 ≥5 样本），否则全局胜率，再否则配置默认值。
position_mult：E[R]≥0.5→1.0；≥0.2→0.85；≥0→0.70；<0→0.50；样本不足→0.85。
"""
from __future__ import annotations

from typing import List, Optional

from tea.config.config_store import Config, load_config
from tea.portfolio import trades as trades_mod


def score_bucket(score: Optional[float], cfg: Optional[Config] = None) -> Optional[int]:
    if score is None:
        return None
    size = int((cfg or load_config()).get("expectancy.bucket_size", 1)) or 1
    return int(score) // size * size


def win_rate(score: Optional[float] = None, cfg: Optional[Config] = None) -> dict:
    """历史胜率：同评分段优先（≥min_samples），否则全局，否则默认。"""
    cfg = cfg or load_config()
    min_n = int(cfg.get("expectancy.min_samples", 5))
    ts = trades_mod.effective_trades(cfg)
    bucket = score_bucket(score, cfg)

    same = [t for t in ts if score_bucket(t.get("total_score"), cfg) == bucket] if bucket is not None else []
    if len(same) >= min_n:
        wins = sum(1 for t in same if t.get("result") == trades_mod.RESULT_WIN)
        return {"p_hat": wins / len(same), "samples": len(same),
                "source": f"同评分段({bucket}分)", "insufficient": False}
    if len(ts) >= min_n:
        wins = sum(1 for t in ts if t.get("result") == trades_mod.RESULT_WIN)
        return {"p_hat": wins / len(ts), "samples": len(ts), "source": "全局胜率", "insufficient": False}
    return {"p_hat": float(cfg.get("expectancy.default_win_rate", 0.40)), "samples": len(ts),
            "source": "默认值（样本不足）", "insufficient": True}


def expected_r(p_hat: float, odds: Optional[float]) -> Optional[float]:
    if odds is None:
        return None
    return p_hat * odds - (1.0 - p_hat)


def position_mult(er: Optional[float], insufficient: bool, cfg: Optional[Config] = None) -> float:
    cfg = cfg or load_config()
    c = lambda k, d: float(cfg.get(f"expectancy.{k}", d))
    if insufficient or er is None:
        return c("mult_insufficient", 0.85)
    if er >= c("mult_high_er", 0.5):
        return c("mult_high", 1.0)
    if er >= c("mult_mid_er", 0.2):
        return c("mult_mid", 0.85)
    if er >= c("mult_low_er", 0.0):
        return c("mult_low", 0.70)
    return c("mult_neg", 0.50)


def evaluate(total_score: Optional[float], odds: Optional[float],
             cfg: Optional[Config] = None) -> dict:
    """期望值评估：p̂ / E[R] / 正负期望 / 仓位乘数。"""
    cfg = cfg or load_config()
    wr = win_rate(total_score, cfg)
    er = expected_r(wr["p_hat"], odds)
    mult = position_mult(er, wr["insufficient"], cfg)
    notes: List[str] = [f"p̂ {wr['p_hat']:.1%}（{wr['source']}，{wr['samples']} 笔）"]
    if odds is not None:
        notes.append(f"E[R] = {wr['p_hat']:.2f}×{odds:.2f} - {1 - wr['p_hat']:.2f} = {er:+.2f}")
    notes.append(f"期望仓位乘数 {mult}")
    return {
        "p_hat": round(wr["p_hat"], 4), "samples": wr["samples"], "source": wr["source"],
        "insufficient": wr["insufficient"], "odds": odds,
        "er": round(er, 3) if er is not None else None,
        "positive": bool(er is not None and er > 0),
        "mult": mult, "notes": notes,
        "breakeven_wr": round(1.0 / (1.0 + odds), 4) if odds else None,
    }


def format_expectancy(e: dict) -> str:
    lines = ["===== 数学期望 ====="]
    for n in e.get("notes", []):
        lines.append(f"  · {n}")
    lines.append(f"  结论：{'正期望' if e.get('positive') else '非正期望'}"
                 + ("（样本不足，按保守乘数处理）" if e.get("insufficient") else ""))
    return "\n".join(lines)
