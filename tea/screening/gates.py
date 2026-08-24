"""法 — 执行门禁：会话门禁 / 代码门禁 / 买入门禁 + 单日状态追踪。

daily_state.json: {"date","new_opens","evaluations","symbol_evals"}
三道门禁分别对应 8.1（run_once 前）、8.2（Phase1）、8.3（Phase4）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tea.analysis.sentiment import CYCLE_CLIMAX, STANCE_EMPTY
from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.core.timing import Timing
from tea.portfolio import plan as plan_mod, portfolio, trades as trades_mod


# ------------------------------------------------------------------ 8.5 单日状态

def state_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("daily_state_file")


def load_state(cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    st = utils.read_json(state_path(cfg), default=None) or {}
    today = utils.today_str()
    if st.get("date") != today:  # 跨日自动归零
        st = {"date": today, "new_opens": 0, "evaluations": 0, "symbol_evals": {}}
    st.setdefault("new_opens", 0)
    st.setdefault("evaluations", 0)
    st.setdefault("symbol_evals", {})
    return st


def save_state(st: dict, cfg: Optional[Config] = None) -> str:
    return utils.write_json(state_path(cfg), st)


def bump_evaluation(code: Optional[str] = None, cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    st = load_state(cfg)
    st["evaluations"] = int(st.get("evaluations", 0)) + 1
    if code:
        c = utils.norm_code(code)
        st["symbol_evals"][c] = int(st["symbol_evals"].get(c, 0)) + 1
    save_state(st, cfg)
    return st


def bump_new_open(cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    st = load_state(cfg)
    st["new_opens"] = int(st.get("new_opens", 0)) + 1
    save_state(st, cfg)
    return st


def reset_state(cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    st = {"date": utils.today_str(), "new_opens": 0, "evaluations": 0, "symbol_evals": {}}
    save_state(st, cfg)
    return st


def symbol_evals(code: str, cfg: Optional[Config] = None) -> int:
    return int(load_state(cfg).get("symbol_evals", {}).get(utils.norm_code(code), 0))


# ------------------------------------------------------------------ 门禁结果

class GateResult:
    def __init__(self):
        self.blocks: List[dict] = []
        self.warnings: List[dict] = []
        self.requires_force = False
        self.context: Dict[str, Any] = {}

    def block(self, rule: str, detail: str) -> "GateResult":
        self.blocks.append({"rule": rule, "detail": detail})
        return self

    def warn(self, rule: str, detail: str) -> "GateResult":
        self.warnings.append({"rule": rule, "detail": detail})
        return self

    @property
    def allowed(self) -> bool:
        return not self.blocks

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "blocks": self.blocks, "warnings": self.warnings,
                "requires_force": self.requires_force, "context": self.context}

    def format(self, title: str = "门禁检查") -> str:
        lines = [f"===== 法 · {title} ====="]
        if self.allowed:
            lines.append("  通过：全部门禁项放行")
        for b in self.blocks:
            lines.append(f"  ✗ [{b['rule']}] {b['detail']}")
        for w in self.warnings:
            lines.append(f"  ! [{w['rule']}] {w['detail']}")
        if self.requires_force:
            lines.append("  需要输入 FORCE 或先完成复盘方可继续")
        return "\n".join(lines)


# ------------------------------------------------------------------ 8.1 会话门禁

def check_session_start(sent: Optional[dict] = None, cfg: Optional[Config] = None,
                        timing: Optional[Timing] = None, force: bool = False,
                        require_window: bool = True) -> GateResult:
    """会话开始门禁（run_once 前检查）。"""
    cfg = cfg or load_config()
    tm = timing or Timing(cfg)
    r = GateResult()
    st = load_state(cfg)
    r.context = {"daily_state": st, "sentiment": sent}

    # 1. 市场姿态 = 空仓
    if sent:
        if sent.get("stance") == STANCE_EMPTY or not sent.get("allow_new"):
            r.block("市场姿态", f"姿态 {sent.get('stance')}（情绪分 {sent.get('score')}，"
                                f"周期 {sent.get('cycle')}）→ 禁止新开评估")
        # 2. 高潮期 + 前5板块均涨 ≥7%
        avg5 = sent.get("avg5")
        if (sent.get("cycle") == CYCLE_CLIMAX and avg5 is not None
                and avg5 >= float(cfg.get("sentiment.climax_block_avg5", 7.0))):
            r.block("高潮易分歧", f"高潮期且前5板块均涨 {avg5:.2f}% ≥7% → 禁止新开")
        # 上证 MA20 下方不再硬拦（原 block_new_eval_when_index_below_ma20）：
        # 弱势市由 9 分共振「大盘趋势」维做分级扣分表达，而不是在会话门禁里一票否决。

    # 3. 今日新开仓已达上限
    max_new = int(cfg.s("daily_max_new_trades", 1))
    if int(st.get("new_opens", 0)) >= max_new:
        r.block("单日新开", f"今日已新开 {st['new_opens']} 笔 ≥ {max_new} → 禁止")

    # 4. 今日评估次数上限
    max_eval = int(cfg.s("daily_max_evaluations", 5))
    if int(st.get("evaluations", 0)) >= max_eval:
        r.block("单日评估", f"今日已评估 {st['evaluations']} 次 ≥ {max_eval} → 禁止")

    # 6. 非标准窗口
    if require_window and cfg.s("block_off_window_eval", True) and not tm.is_buy_window():
        r.block("交易窗口", f"当前 {tm.phase()}，非标准窗口 {tm.buy_window_text()} → 禁止新开评估")

    # 7. 连亏冷却
    limit = int(cfg.s("consec_loss_limit", 2))
    cl = trades_mod.consec_losses(cfg)
    if cl >= limit:
        if force:
            r.warn("连亏冷却", f"连亏 {cl} 笔 ≥ {limit}，已 FORCE 放行（请确认已复盘）")
        else:
            r.block("连亏冷却", f"连亏 {cl} 笔 ≥ {limit} → 需输入 FORCE 或先复盘")
            r.requires_force = True
    r.context["consec_losses"] = cl
    return r


# ------------------------------------------------------------------ 8.2 代码门禁

def check_code_gate(code: str, sent: Optional[dict] = None, cfg: Optional[Config] = None,
                    timing: Optional[Timing] = None) -> GateResult:
    """Phase1 代码门禁：单日限额 / 同票复筛 / 计划绑定（MA20 下方不再硬拦）。"""
    cfg = cfg or load_config()
    code = utils.norm_code(code)
    r = GateResult()
    st = load_state(cfg)

    max_eval = int(cfg.s("daily_max_evaluations", 5))
    if int(st.get("evaluations", 0)) >= max_eval:
        r.block("单日评估", f"今日评估 {st['evaluations']} 次 ≥ {max_eval} → 禁止")

    max_sym = int(cfg.s("daily_max_symbol_evaluations", 2))
    n_sym = symbol_evals(code, cfg)
    if n_sym >= max_sym:
        r.block("同票复筛", f"{code} 今日已评估 {n_sym} 次 ≥ {max_sym} → 禁止")

    if cfg.s("require_plan_for_new_open", True):
        plan = plan_mod.load_plan(cfg)
        if not plan_mod.is_valid_today(plan, cfg):
            r.block("计划绑定", f"今日无有效计划（状态 {plan.get('status')}，"
                                f"执行日 {plan.get('execute_date')}）→ 禁止评估（核心纪律）")
        elif code not in plan_mod.planned_codes(plan):
            r.block("计划绑定",
                    f"{code} 不在今日计划内（{'、'.join(plan_mod.planned_labels(plan))}）→ 禁止评估")
        r.context["plan"] = plan

    if portfolio.has_position(code, cfg):
        r.warn("已有持仓", f"{code} 当前已有持仓，本次评估视为加仓/确认仓场景")
    r.context["symbol_evals"] = n_sym
    return r


# ------------------------------------------------------------------ 8.3 买入门禁

def check_buy_gate(code: str, ev: Optional[dict] = None, sent: Optional[dict] = None,
                   cfg: Optional[Config] = None, timing: Optional[Timing] = None,
                   force: bool = False) -> GateResult:
    """Phase4 最终买入门禁。"""
    cfg = cfg or load_config()
    tm = timing or Timing(cfg)
    code = utils.norm_code(code)
    r = GateResult()
    st = load_state(cfg)

    if cfg.s("require_standard_window_for_buy", True) and not tm.is_buy_window():
        r.block("买入窗口", f"当前 {tm.phase()}，非 {tm.buy_window_text()} → 禁止新开")

    max_new = int(cfg.s("daily_max_new_trades", 1))
    if int(st.get("new_opens", 0)) >= max_new:
        r.block("单日新开", f"今日已新开 {st['new_opens']} 笔 ≥ {max_new} → 禁止")

    if cfg.s("require_plan_for_new_open", True) and not plan_mod.in_plan_today(code, cfg):
        r.block("计划绑定", f"{code} 不在今日有效计划内 → 禁止买入")

    if sent and (sent.get("stance") == STANCE_EMPTY or not sent.get("allow_new")):
        r.block("市场姿态", f"姿态 {sent.get('stance')} / 不允许新开 → 禁止买入")

    if ev:
        total, th = ev.get("total_score"), ev.get("pass_threshold")
        if total is not None and th is not None and total < th:
            r.block("共振门槛", f"共振分 {total} < 有效门槛 {th} → 禁止买入")
        vt = ev.get("veto") or {}
        if vt.get("rejected"):
            r.block("VETO", "存在硬否决：" + "；".join(i["label"] for i in vt.get("hard", [])))
        elif vt.get("soft"):
            r.block("VETO", "存在软否决：" + "；".join(i["label"] for i in vt.get("soft", []))
                    + " → 进观察轨，不买")
        odds = (ev.get("levels") or {}).get("odds")
        min_odds = float(cfg.s("min_odds", 3))
        if odds is None or odds < min_odds:
            r.block("盈亏比", f"R:R {utils.num(odds)} < {min_odds:.0f} → 禁止买入")

    cap = portfolio.available_cash(cfg)
    if cap <= 0:
        r.block("可用资金", f"可用资金 {utils.money(cap)} → 无法开仓")

    if force and r.blocks:
        r.warn("FORCE", "已请求强制放行，但门禁项属纪律硬约束，不接受 FORCE")
    return r


# ------------------------------------------------------------------ 综合状态

def status(sent: Optional[dict] = None, cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    st = load_state(cfg)
    tm = Timing(cfg)
    plan = plan_mod.load_plan(cfg)
    return {
        "date": st.get("date"),
        "phase": tm.phase(),
        "buy_window": tm.is_buy_window(),
        "new_opens": st.get("new_opens"),
        "max_new": int(cfg.s("daily_max_new_trades", 1)),
        "evaluations": st.get("evaluations"),
        "max_evaluations": int(cfg.s("daily_max_evaluations", 5)),
        "symbol_evals": st.get("symbol_evals"),
        "consec_losses": trades_mod.consec_losses(cfg),
        "plan_status": plan.get("status"),
        "plan_valid_today": plan_mod.is_valid_today(plan, cfg),
        "plan_codes": plan_mod.planned_codes(plan),
        "plan_labels": plan_mod.planned_labels(plan),
        "positions": len(portfolio.positions(cfg)),
        "capital": portfolio.get_capital(cfg),
        "available": portfolio.available_cash(cfg),
        "stance": (sent or {}).get("stance"),
    }


def format_status(s: dict) -> str:
    return "\n".join([
        "===== 法 · 今日状态 =====",
        f"日期 {s['date']}  {s['phase']}  买入窗口 {'开' if s['buy_window'] else '关'}",
        f"新开 {s['new_opens']}/{s['max_new']}  评估 {s['evaluations']}/{s['max_evaluations']}  "
        f"连亏 {s['consec_losses']}",
        f"计划 {s['plan_status']}（今日有效={'是' if s['plan_valid_today'] else '否'}）"
        f" 标的 {'、'.join(s.get('plan_labels') or []) or '—'}",
        f"持仓 {s['positions']} 笔  总资金 {utils.money(s['capital'])}  可用 {utils.money(s['available'])}",
    ])
