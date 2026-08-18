"""VETO 一票否决（触发即 REJECT，不占 9 分体系的分数）。

硬否决（涨停区、ST、换手过高、无权限、极端乖离）→ 直接 REJECT。
软否决（分时高位、乖离、追高、接近涨停）→ 进观察轨，等回踩再评。
20cm 板（创业板/科创板）阈值按涨停幅等比放大。
"""
from __future__ import annotations

from typing import List, Optional

from tea.analysis.identity import TIER_LEADER
from tea.config.config_store import Config, load_config
from tea.core import utils

KIND_HARD = "hard"
KIND_SOFT = "soft"

BOARD_NAMES = {"main": "主板", "gem": "创业板", "star": "科创板", "bse": "北交所"}


def _scale(pct_threshold: float, quote: dict, cfg: Optional[Config] = None) -> float:
    """按标的涨停幅缩放阈值（10cm 基准）。

    base 被配成 0/负数（配置写错）时静默回落为不缩放，而不是 ZeroDivisionError
    炸掉整个 VETO 检查；本函数是纯函数拿不到 io，所以不做提示。
    cap 拿不到（None/0）时回落到代码/名称推导的涨停幅，仍拿不到则不缩放。
    """
    cfg = cfg or load_config()
    base = utils.to_float(cfg.get("veto.limit_up_pct_base", 10.0), 0.0) or 0.0
    if base <= 0:
        return pct_threshold
    cap = utils.to_float(quote.get("limit_up_pct"), None) or utils.limit_up_pct(
        quote.get("code", ""), quote.get("name", ""))
    if not cap or cap <= 0:
        return pct_threshold
    return pct_threshold * (cap / base)


def board_allowed(code: str, cfg: Optional[Config] = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get(f"permissions.{utils.board_of(code)}", True))


def bias_limit(identity: Optional[dict], cfg: Config) -> float:
    """乖离否决阈值：龙头 20%，普通 15%。"""
    leader = (identity or {}).get("tier") == TIER_LEADER
    return float(cfg.s("veto_bias_leader_pct", 20.0) if leader else cfg.s("veto_bias_normal_pct", 15.0))


def intraday_limit(identity: Optional[dict], cfg: Config) -> float:
    """分时高位否决阈值：普通 0.75，龙头 0.85。"""
    ident = identity or {}
    leader = ident.get("tier") == TIER_LEADER
    relax = leader or (
        (ident.get("score") or 0) >= float(cfg.get("scoring.leader_intraday_relax_score", 90))
        and (ident.get("sector_rank") or 999) <= int(cfg.get("scoring.leader_intraday_relax_rank", 3)))
    return float(cfg.get("scoring.intraday_leader_pct", 0.85) if relax
                 else cfg.s("veto_intraday_high_pct", 0.75))


def check(quote: dict, ind: Optional[dict] = None, identity: Optional[dict] = None,
          intraday: Optional[float] = None, cfg: Optional[Config] = None,
          in_session: Optional[bool] = None) -> dict:
    """执行全部否决项检查。

    in_session=None 时视为「盘中」，分时否决始终生效（自测/交互直接调用时的向后
    兼容行为）；preflight.evaluate 会显式传入 Timing 判定的会话状态，从而在盘前/
    午间/盘后跳过分时否决（skip_intraday_check_off_session=True 时）并留痕。
    """
    cfg = cfg or load_config()
    ind = ind or {}
    items: List[dict] = []

    # 分时否决是否生效：仅在盘中交易时段执行（可配）。盘后扫描时现价≈当日最高，
    # 分时位置≈1.0 是强势股常态而非追高，跳过并留痕到 intraday_skipped/intraday_note。
    if in_session is None:
        in_session = True
    intraday_active = bool(cfg.get("veto.check_intraday", True))
    intraday_skipped = False
    intraday_note = ""
    if intraday_active and not in_session and cfg.get("veto.skip_intraday_check_off_session", True):
        intraday_active = False
        intraday_skipped = True
        intraday_note = "非盘中时段（盘前/午间/盘后），分时位置失真，跳过追高/封顶否决"

    def veto(name: str, label: str, kind: str, detail: str,
             value=None, threshold=None) -> None:
        items.append({"name": name, "label": label, "kind": kind, "detail": detail,
                      "value": value, "threshold": threshold})

    code = quote.get("code") or ""
    name = quote.get("name") or ""
    chg = quote.get("chg_pct")
    turnover = quote.get("turnover")
    bias = ind.get("bias_ma20")

    # ① 板块权限
    if cfg.get("veto.check_board_permission", True) and not board_allowed(code, cfg):
        veto("board_permission", "板块无权限", KIND_HARD,
             f"{BOARD_NAMES.get(utils.board_of(code), '未知')}未开通交易权限")

    # ② ST
    if cfg.get("veto.check_st", True) and (quote.get("is_st") or utils.is_st(name)):
        veto("st", "ST 标的", KIND_HARD, f"名称含 ST/*：{name}（风险过高）")

    # ③ 涨停 / 接近涨停
    if cfg.get("veto.check_limit_up", True) and chg is not None:
        zone = _scale(float(cfg.s("veto_limit_zone_pct", 9.5)), quote, cfg)
        near = _scale(float(cfg.s("veto_near_limit_pct", 9.0)), quote, cfg)
        if chg >= zone:
            veto("limit_up", "涨停/封板", KIND_HARD,
                 f"涨幅 {chg:.2f}% ≥ {zone:.2f}%（涨停区，不可追板）", chg, zone)
        elif chg >= near:
            veto("near_limit_up", "接近涨停", KIND_SOFT,
                 f"涨幅 {chg:.2f}% ≥ {near:.2f}%（接近涨停，等回踩）", chg, near)

    # ④ 换手过高
    if cfg.get("veto.check_turnover", True) and turnover is not None:
        tmax = float(cfg.s("veto_max_turnover", 25.0))
        if turnover > tmax:
            veto("turnover", "换手过高", KIND_HARD,
                 f"换手 {turnover:.2f}% > {tmax:.0f}%（筹码混乱）", turnover, tmax)

    # ⑤ MA20 乖离
    if cfg.get("veto.check_bias", True) and bias is not None:
        hard = float(cfg.s("veto_bias_hard_max_pct", 30.0))
        soft = bias_limit(identity, cfg)
        if bias > hard:
            veto("bias_hard", "乖离极端", KIND_HARD,
                 f"MA20 乖离 {bias:.2f}% > {hard:.0f}%（硬上限）", bias, hard)
        elif bias > soft:
            veto("bias_ma20", "MA20 乖离过热", KIND_SOFT,
                 f"MA20 乖离 {bias:.2f}% > {soft:.0f}%（过热，等回踩）", bias, soft)

    # ⑥ 分时高位（仅盘中生效；非盘中且开启跳过时被静默略过并留痕）
    if intraday_active and intraday is not None:
        hard = float(cfg.s("veto_intraday_hard_pct", 0.95))
        soft = intraday_limit(identity, cfg)
        if intraday >= hard:
            veto("intraday_hard", "分时封顶", KIND_HARD,
                 f"分时位置 {intraday:.0%} ≥ {hard:.0%}（硬否决）", intraday, hard)
        elif intraday > soft:
            veto("intraday_high", "分时高位", KIND_SOFT,
                 f"分时位置 {intraday:.0%} > {soft:.0%}（追高风险，等回踩）", intraday, soft)

    hard_items = [i for i in items if i["kind"] == KIND_HARD]
    soft_items = [i for i in items if i["kind"] == KIND_SOFT]
    return {
        "items": items,
        "hard": hard_items,
        "soft": soft_items,
        "has_veto": bool(items),
        "rejected": bool(hard_items),
        "watchable": bool(soft_items) and not hard_items,
        "labels": [i["label"] for i in items],
        "reason": "；".join(i["detail"] for i in items) if items else "",
        # 留痕：分时否决是否因非盘中而被跳过（供报告/scan_details/观察池快照追溯）
        "intraday_skipped": intraday_skipped,
        "intraday_note": intraday_note,
    }


def overheat_bias_limit(stage: str, identity: Optional[dict], cfg: Optional[Config] = None) -> float:
    """过热判定用的乖离阈值（分层：普通 8%、萌芽龙头 12%、突破龙头 15%）。"""
    cfg = cfg or load_config()
    leader = (identity or {}).get("tier") == TIER_LEADER
    if leader and stage == "萌芽":
        return float(cfg.get("scoring.overheat_bias_sprout_leader", 12.0))
    if leader:
        return float(cfg.get("scoring.overheat_bias_break_leader", 15.0))
    return float(cfg.get("scoring.overheat_bias_normal", 8.0))


def format_veto(result: dict) -> str:
    if not result.get("items") and not result.get("intraday_skipped"):
        return "VETO 检查：全部通过"
    lines = ["VETO 检查：触发 %d 项" % len(result["items"])] if result.get("items") else ["VETO 检查：全部通过"]
    for i in result["items"]:
        tag = "硬否决" if i["kind"] == KIND_HARD else "软否决"
        lines.append(f"  [{tag}] {i['label']} — {i['detail']}")
    if result.get("intraday_skipped"):
        lines.append(f"  [提示] {result.get('intraday_note') or '分时否决已跳过（非盘中）'}")
    if result["rejected"]:
        lines.append("→ 结论：REJECT（硬否决，直接放弃）")
    elif result["watchable"]:
        lines.append("→ 结论：进观察轨（软否决，等回踩再评）")
    return "\n".join(lines)
