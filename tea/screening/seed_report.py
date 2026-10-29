"""种子扫描报告生成：SEED_*.md（Markdown 存档）+ 控制台摘要。

verdict：HAS_TRADEABLE（有可买）/ PENDING（仅观察）/ EMPTY（主动不开新仓）。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from tea.config.config_store import Config, load_config
from tea.analysis.sentiment import format_sentiment_summary
from tea.core import notify, utils
from tea.data.indicators import format_bollinger
from . import preflight
from .screener import (VERDICT_EMPTY, VERDICT_PENDING, VERDICT_TRADEABLE,
                       dyn_window_text)


def _hl(text: str, color: str = "") -> str:
    """ANSI 高亮（复用 utils.hl，默认警告黄）。"""
    return utils.hl(text, color or utils.COLOR_WARN)


def _sty(text: str, color: str, style: str = "console") -> str:
    """控制台 ANSI / 邮件纯文本。"""
    if style == "console":
        return _hl(text, color)
    return text


def _md_em(text: str) -> str:
    """Markdown 加粗（核心数值）；占位符不包。"""
    if not text or text == "—":
        return text or "—"
    return f"**{text}**"


def _hl_price(v: Optional[float], nd: int = 2, width: int = 0, style: str = "console") -> str:
    text = utils.num(v, nd)
    if width:
        text = f"{text:>{width}}"
    return _sty(text, utils.COLOR_NEUTRAL, style)


def _hl_chg(v: Optional[float], width: int = 0, style: str = "console") -> str:
    text = utils.pct(v)
    if width:
        text = f"{text:>{width}}"
    return _sty(text, utils.sign_color(v), style)


def _reso_max(ev: Optional[dict] = None, *, max_score: Optional[int] = None) -> int:
    if max_score is not None:
        return int(max_score)
    if ev:
        sc = ev.get("scoring") or {}
        if sc.get("max") is not None:
            return int(sc["max"])
    return preflight.RESONANCE_SCORE_MAX


def _hl_reso(score: Optional[int], threshold: Optional[int],
             max_score: Optional[int] = None, ev: Optional[dict] = None,
             style: str = "console") -> str:
    mx = _reso_max(ev, max_score=max_score)
    return preflight.format_resonance_score(score, threshold, mx, style=style)


def _hl_winrate(score: Optional[int], threshold: int, max_score: int,
                style: str = "console") -> str:
    return preflight.format_winrate_score(score, threshold, max_score, style=style)


def _hl_t3_prob(prob: Optional[float], min_prob: Optional[float], cfg: Config,
                style: str = "console") -> str:
    return preflight.format_winrate_t3_prob(prob, min_prob, cfg, style=style)


def _score_legend_seed(cfg: Config) -> str:
    wr_th = int(cfg.get("winrate.buyable_threshold", 3))
    t3_th = float(cfg.get("winrate.t3_prob.min_prob", 0.35))
    return (
        f"  分制说明：共振=得分/满分（六维，门槛动态，纪律型主通道）；"
        f"胜率=得分/{preflight.WINRATE_SCORE_MAX}、门槛≥{wr_th}；"
        f"T+3获利=综合概率、门槛≥{t3_th:.0%}（实证对照，非收益承诺）"
    )


def _score_legend_winrate(threshold: int, max_score: int, cfg: Config) -> str:
    t3_th = float(cfg.get("winrate.t3_prob.min_prob", 0.35))
    return (
        f"  分制说明：胜率=得分/满分{max_score}、门槛≥{threshold}；"
        f"T+3获利=综合概率、门槛≥{t3_th:.0%}（实证对照）；"
        f"共振=得分/满分{preflight.RESONANCE_SCORE_MAX}、门槛动态（纪律型，种子主通道）"
    )


def _hl_sl_pct(v: Optional[float]) -> str:
    return _hl(utils.pct(v), utils.COLOR_LOSS)


def _hl_tp_pct(v: Optional[float]) -> str:
    return _hl(utils.pct(v), utils.COLOR_PROFIT)


def _hl_odds(v: Optional[float]) -> str:
    return _hl(utils.num(v), utils.COLOR_INFO)

VERDICT_LABEL = {
    VERDICT_TRADEABLE: "HAS_TRADEABLE（有可买标的，已写次日计划）",
    VERDICT_PENDING: "PENDING（无可买，仅观察轨跟踪）",
    VERDICT_EMPTY: "EMPTY（主动不开新仓 — 宁缺毋滥）",
}


# ------------------------------------------------------------------ 小工具

def _sent_line(sent: Optional[dict], style: str = "console") -> str:
    return format_sentiment_summary(sent, style=style)


def _tech_indicators_line(ev: dict) -> Optional[str]:
    """技术观测一行：乖离 + 布林（只展示，不参与裁决）。"""
    ind = ev.get("ind") or {}
    parts: List[str] = []
    bias = ind.get("bias_ma20")
    if bias is not None:
        parts.append(f"乖离 {utils.pct(bias)}")
    bb = format_bollinger(ind)
    if bb != "布林 —":
        parts.append(bb)
    return " ｜ ".join(parts) if parts else None


def _tech_indicators_line_cand(c: dict) -> Optional[str]:
    """候选明细行上的技术观测（字段在 cand 顶层）。"""
    parts: List[str] = []
    if c.get("bias_ma20") is not None:
        parts.append(f"乖离 {utils.pct(c.get('bias_ma20'))}")
    bb = format_bollinger(c)
    if bb != "布林 —":
        parts.append(bb)
    return " ｜ ".join(parts) if parts else None


def _ev_row(ev: dict) -> str:
    q = ev.get("quote") or {}
    idn = ev.get("identity") or {}
    lv = ev.get("levels") or {}
    ft = ev.get("followthrough") or {}
    score = ev.get("total_score")
    threshold = ev.get("pass_threshold")
    reso = preflight.format_resonance_score(
        score, threshold, _reso_max(ev), style="md")
    return (f"| {ev.get('code')} | {ev.get('name')} | {ev.get('sector_name') or (ev.get('sector') or {}).get('name') or '—'} "
            f"| {_md_em(utils.num(q.get('price')))} | {_md_em(utils.pct(q.get('chg_pct')))} "
            f"| {reso} "
            f"| {idn.get('tier')} {utils.num(idn.get('score'), 1)} "
            f"| {(ev.get('stage') or {}).get('stage') or '—'} "
            f"| {_md_em(utils.pct(lv.get('sl_pct')))} / {_md_em(utils.pct(lv.get('tp_pct')))} "
            f"| {_md_em(utils.num(lv.get('odds')))} "
            f"| {utils.num(ft.get('score'), 1) if ft.get('score') is not None else '—'} |")


_EV_HEADER = [
    "| 代码 | 名称 | 板块 | 现价 | 涨幅 | 共振 | 身份 | 阶段 | 止损/止盈 | R:R | 跟涨分 |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
]

_CAND_HEADER = [
    "| 代码 | 名称 | 板块 | 涨幅 | 分时位 | 共振 | 身份 | 结果 | 原因 |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
]


def _intr(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0%}"


def _reso(c: dict) -> str:
    return preflight.format_resonance_score(
        c.get("score"), c.get("threshold"), c.get("scoring_max"), style="plain")


def _ident(c: dict, nd: int = 1) -> str:
    if c.get("identity_tier") is None and c.get("identity_score") is None:
        return "—"
    return f"{c.get('identity_tier') or '—'} {utils.num(c.get('identity_score'), nd)}"


def format_t3_expect_hint(c: dict, style: str = "plain") -> Optional[str]:
    """计划止盈路径 T+1/T+2/T+3（兼容旧名，见 preflight.format_tn_plan_hint）。"""
    return preflight.format_tn_plan_hint(c, style=style)


def format_order_hint(c: dict, style: str = "plain") -> Optional[str]:
    """建议挂单价一行（候选明细 / 报告复用）。

    style: plain（纯文本）| console（ANSI）| md（Markdown 加粗）
    """
    op = c.get("order_price")
    if op is None:
        return None
    basis = c.get("order_basis") or ""
    stop = c.get("stop")
    target = c.get("target")
    if style == "console":
        op_s = _hl(utils.num(op), utils.COLOR_WARN)
        stop_s = _hl(utils.num(stop), utils.COLOR_LOSS) if stop is not None else "—"
        target_s = _hl(utils.num(target), utils.COLOR_PROFIT) if target is not None else "—"
    elif style == "md":
        op_s = _md_em(utils.num(op))
        stop_s = _md_em(utils.num(stop)) if stop is not None else "—"
        target_s = _md_em(utils.num(target)) if target is not None else "—"
    else:
        op_s = utils.num(op)
        stop_s = utils.num(stop)
        target_s = utils.num(target)
    line = f"建议挂单价：{op_s}" + (f"（{basis}）" if basis else "")
    if stop is not None or target is not None:
        line += f"  止损：{stop_s}　止盈：{target_s}"
    return line


def cand_display_reason(c: dict) -> str:
    """候选明细展示用原因：优先 reason，否决类用 veto_labels / veto_reason 兜底。"""
    reason = (c.get("reason") or "").strip()
    if reason:
        return reason
    labels = [x for x in (c.get("veto_labels") or []) if x]
    if labels:
        verdict = c.get("verdict") or "否决"
        return f"{verdict}：" + "；".join(labels)
    veto_reason = (c.get("veto_reason") or "").strip()
    if veto_reason:
        return veto_reason
    return "—"


def _cand_row(c: dict) -> str:
    reso = _reso(c)
    score, threshold = c.get("score"), c.get("threshold")
    if score is not None and threshold is not None:
        reso = _md_em(reso)
    return (f"| {c.get('code')} | {c.get('name')} | {c.get('sector_name') or '—'} "
            f"| {_md_em(utils.pct(c.get('chg')))} | {_md_em(_intr(c.get('intraday')))} | {reso} "
            f"| {_ident(c)} "
            f"| **{c.get('verdict') or '—'}** | {cand_display_reason(c)} |")


def _dyn_line(result: dict) -> Optional[str]:
    """当前生效的涨幅窗口下限（随最强板块自适应），让用户知道用的是什么阈值。"""
    win = result.get("dyn_window")
    return f"动态窗口：{dyn_window_text(win)}" if win else None


def _scoring_dims_lines(ev: dict) -> List[str]:
    """共振六维逐项展开（Markdown 列表），供种子文档复盘「每一分从哪来」。

    dims 既可能来自完整预审的 `scoring.dims`（带 no），也可能来自候选明细的
    `scoring_dims`（不带 no）；两种都兼容，缺 no 时用顺序号补齐。
    """
    sc = ev.get("scoring") or {}
    dims = sc.get("dims") or ev.get("scoring_dims") or []
    if not dims:
        return []
    total = sc.get("total")
    if total is None:
        total = ev.get("total_score")
    maxv = sc.get("max")
    lines = [f"  - 共振六维（{total}/{maxv}）："]
    for i, d in enumerate(dims, 1):
        no = d.get("no") or i
        lines.append(f"    - {no}. {d.get('name')} {d.get('score')}/{d.get('max')} — {d.get('detail')}")
    return lines


# ------------------------------------------------------------------ Markdown

def render_md(result: dict, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    verdict = result.get("verdict", VERDICT_EMPTY)
    lines: List[str] = [
        f"# SEED {utils.today_str()} · {result.get('scan_id') or ''}".rstrip(),
        "",
        f"- 扫描时间：{result.get('at')}",
        f"- **裁决：{VERDICT_LABEL.get(verdict, verdict)}**",
        f"- 市场天气：{_sent_line(result.get('sentiment'))}",
        f"- 启用档位：{result.get('tier') or '—'}",
        f"- 漏斗：板块 TOP{len(result.get('sectors') or [])} → 初筛 {result.get('candidates_n', 0)} 只 "
        f"→ VETO 通过 {result.get('veto_passed_n', 0)} 只（软否决 {result.get('soft_n', 0)} 只）"
        f"→ 可买 {len(result.get('buyable') or [])} 只",
    ]
    dyn = _dyn_line(result)
    if dyn:
        lines.append(f"- {dyn}")
    lines.append("")

    # ---- 第 1 步 板块
    lines += ["## 第 1 步 · 板块综合排序", ""]
    sectors = result.get("sectors") or []
    if sectors:
        lines += ["| 排名 | 板块 | 涨幅 | 涨停 | 温和票 | 热度分 | 结构分 | 影子 | 综合分 | 门槛 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for s in sectors:
            lines.append(f"| {s.get('rank')} | {s.get('name')} | {_md_em(utils.pct(s.get('chg')))} "
                         f"| {s.get('limit_up_count')} | {s.get('mild_n')} "
                         f"| {utils.num(s.get('heat_score'), 1)} | {utils.num(s.get('mild_score'), 1)} "
                         f"| {'+%.0f' % s['shadow_bonus'] if s.get('shadow_bonus') else '—'} "
                         f"| {utils.num(s.get('total_score'), 1)} | {s.get('gate') or '—'} |")
    else:
        lines.append("无板块通过硬门槛（排名≤8 且涨停≥2 家 / 涨停 1 家+综合分≥60）。")
    lines.append("")

    # ---- 三档输出
    for title, key, note in (
        ("## 输出一 · 可买（追高，写次日计划，T+1 14:00 执行）", "buyable", "无可买标的。"),
        ("## 输出二 · 待启动观察（盯触发条件，不自动买入）", "watch", "观察轨为空。"),
        ("## 输出三 · 近失（只复盘，不盯盘）", "near_miss", "无近失记录。"),
    ):
        evs = result.get(key) or []
        lines += [title, ""]
        if not evs:
            lines += [note, ""]
            continue
        lines += _EV_HEADER + [_ev_row(e) for e in evs] + [""]
        for e in evs:
            detail = []
            if e.get("track"):
                detail.append(f"轨道 {e['track']}")
            if e.get("triggers"):
                detail.append("触发条件：" + "；".join(e["triggers"]))
            if e.get("reasons"):
                detail.append("扣分/拒绝：" + "；".join(e["reasons"]))
            vt = e.get("veto") or {}
            if vt.get("soft"):
                detail.append("软否决：" + "；".join(i["label"] for i in vt["soft"]))
            if detail:
                lines.append(f"- **{e.get('code')} {e.get('name')}** — " + " ｜ ".join(detail))
            if key in ("buyable", "watch", "eve"):
                order_line = format_order_hint(e, style="md")
                if order_line:
                    lines.append(f"  - {order_line}")
                t3_line = format_t3_expect_hint(e, style="md")
                if t3_line:
                    lines.append(f"  - {t3_line}")
            tech = _tech_indicators_line(e)
            if tech:
                lines.append(f"  - 技术观测：{tech}")
            lines += _scoring_dims_lines(e)
        lines.append("")

    # ---- 低吸（启动前夕）观察
    eve = result.get("eve") or []
    ew = result.get("eve_window") or [1.0, 3.0]
    lines += [f"## 低吸观察（启动前夕 {ew[0]:.1f}%~{ew[1]:.1f}% 涨幅窗 · 暂不写计划，积累样本）", ""]
    if eve:
        lines += _EV_HEADER + [_ev_row(e) for e in eve] + [""]
        for e in eve:
            if e.get("triggers"):
                lines.append(f"- {e.get('code')} {e.get('name')} — 触发条件："
                             + "；".join(e["triggers"]))
            order_line = format_order_hint(e, style="md")
            if order_line:
                lines.append(f"  - {order_line}")
            t3_line = format_t3_expect_hint(e, style="md")
            if t3_line:
                lines.append(f"  - {t3_line}")
            tech = _tech_indicators_line(e)
            if tech:
                lines.append(f"  - 技术观测：{tech}")
            lines += _scoring_dims_lines(e)
        lines.append("")
    else:
        lines += ["无低吸观察标的。", ""]

    # ---- 候选明细（初筛后逐只的最终裁决，硬否决/数据缺也在这里）
    cands = result.get("candidates") or []
    cn = result.get("candidates_n", len(cands))
    lines += [f"## 候选明细（初筛 {cn} 只 → 淘汰/保留原因）", ""]
    if cands:
        lines += _CAND_HEADER + [_cand_row(c) for c in cands] + [""]
        for c in cands:
            extras: List[str] = []
            order_line = format_order_hint(c, style="md")
            if order_line:
                extras.append(order_line)
            t3_line = format_t3_expect_hint(c, style="md")
            if t3_line:
                extras.append(t3_line)
            tech = _tech_indicators_line_cand(c)
            if tech:
                extras.append(f"技术观测：{tech}")
            if extras:
                lines.append(f"- **{c.get('code')} {c.get('name')}** — " + " ｜ ".join(extras))
        lines.append("")
    else:
        lines += ["无候选（初筛未通过任何标的）。", ""]

    # ---- 备注 / 落选
    if result.get("notes"):
        lines += ["## 扫描备注", ""] + [f"- {n}" for n in result["notes"]] + [""]
    trace = result.get("trace") or {}
    if trace:
        md_path = trace.get("md")
        jl_path = trace.get("jsonl")
        if md_path:
            md_path = os.path.relpath(md_path)
        if jl_path:
            jl_path = os.path.relpath(jl_path)
        lines += ["## 落选追溯", "",
                  f"- 人类可读：`{md_path}`",
                  f"- 机器可读：`{jl_path}`", ""]

    lines += ["---", "",
              f"> 生成于 {utils.now().strftime('%Y-%m-%d %H:%M:%S')} · "
              f"pass_threshold={cfg.s('pass_threshold', 6)} min_odds={cfg.s('min_odds', 3)}"]
    return "\n".join(lines)


def write_report(result: dict, cfg: Optional[Config] = None) -> Optional[str]:
    """落盘 SEED_<stamp>.md，返回路径（配置关闭时返回 None）。"""
    cfg = cfg or load_config()
    if not cfg.get("report.write_seed_report", True):
        return None
    prefix = cfg.get("report.seed_prefix", "SEED")
    path = cfg.report_file(f"{prefix}_{utils.stamp()}.md")
    out = utils.atomic_write(path, render_md(result, cfg))
    utils.cleanup_reports(cfg)
    return out


def latest_report_path(cfg: Optional[Config] = None,
                       prefer_today: bool = True) -> Optional[str]:
    """返回最新 SEED 报告路径；prefer_today 时优先当日最新一份。"""
    cfg = cfg or load_config()
    prefix = str(cfg.get("report.seed_prefix", "SEED"))
    d = cfg.reports_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return None
    today_tag = utils.now().strftime("%Y%m%d")
    candidates: List[tuple] = []
    for name in names:
        if not name.startswith(f"{prefix}_") or not name.lower().endswith(".md"):
            continue
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        candidates.append((mtime, path, name))
    if not candidates:
        return None
    if prefer_today:
        today_hits = [c for c in candidates if today_tag in c[2]]
        if today_hits:
            return max(today_hits, key=lambda x: x[0])[1]
    return max(candidates, key=lambda x: x[0])[1]


def show_latest(cfg: Optional[Config] = None, io: Any = None,
                prefer_today: bool = True) -> int:
    """只读展示最新 SEED 报告（不触发扫描、不写样本）。"""
    cfg = cfg or load_config()
    if io is None:
        io = type("IO", (), {"say": lambda self, msg: print(msg, file=sys.stdout)})()
    path = latest_report_path(cfg, prefer_today=prefer_today)
    if not path:
        io.say("  暂无 SEED 报告")
        io.say("  种子扫描由 launchd 工作日 14:30 自动执行；胜率影子 14:35；漏扫见 ops/05、ops/10")
        return 1
    io.say(f"  只读 · {path}")
    io.say("  提示：菜单不提供手动种子扫描，避免污染 seed_records 样本锚点（14:30）")
    io.say("")
    try:
        with open(path, encoding="utf-8") as f:
            io.say(f.read().rstrip())
    except OSError as exc:
        io.say(f"  ! 读取失败：{exc}")
        return 1
    return 0


# ------------------------------------------------------------------ 控制台

def format_result(result: dict, cfg: Optional[Config] = None) -> str:
    """控制台摘要（不含 Markdown 表格）。"""
    cfg = cfg or load_config()
    verdict = result.get("verdict", VERDICT_EMPTY)
    wr_th = int(cfg.get("winrate.buyable_threshold", 3))
    lines = [
        "===== 种子扫描结果 =====",
        f"  时间 {result.get('at')}   裁决 {VERDICT_LABEL.get(verdict, verdict)}",
        f"  {_sent_line(result.get('sentiment'))}",
        f"  启用档位 {result.get('tier') or '—'}"
        f"   漏斗 初筛 {result.get('candidates_n', 0)} → VETO 过 {result.get('veto_passed_n', 0)}"
        f" → 可买 {len(result.get('buyable') or [])}",
        _score_legend_seed(cfg),
    ]
    dyn = _dyn_line(result)
    if dyn:
        lines.append(f"  {dyn}")

    sectors = result.get("sectors") or []
    lines.append(f"  ---- 第1步 板块 TOP{len(sectors)} ----")
    if sectors:
        for s in sectors:
            lines.append(f"    #{s.get('rank'):<3} {s.get('name'):<10} {_hl_chg(s.get('chg'), 8)}"
                         f"  涨停 {s.get('limit_up_count')} 家  温和票 {s.get('mild_n')} 只"
                         f"  综合 {_hl(utils.num(s.get('total_score'), 1), utils.COLOR_INFO)}  [{s.get('gate') or '—'}]")
    else:
        lines.append("    无板块达标")

    op_map = {
        "buyable": ("买入", utils.COLOR_SEED),     # 种子选中 → 品红（与盈亏色区分）
        "watch": ("观察", utils.COLOR_WARN),       # 待定观察 → 黄
        "near_miss": ("不操作", utils.COLOR_INFO),  # 只复盘 → 青
        "eve": ("观察", utils.COLOR_INFO),          # 前夕观察 → 青
    }
    for title, key in (("可买（追高）", "buyable"), ("待启动观察", "watch"),
                       ("近失（只复盘）", "near_miss"), ("低吸观察（启动前夕）", "eve")):
        evs = result.get(key) or []
        op_label, color = op_map.get(key, ("", utils.COLOR_WARN))
        lines.append(f"  ---- {title}（{len(evs)}）----")
        if not evs:
            lines.append("    —")
            continue
        for e in evs:
            q = e.get("quote") or {}
            idn = e.get("identity") or {}
            lv = e.get("levels") or {}
            wr_part = ""
            if e.get("winrate_score") is not None:
                wr_part = f"  胜率 {_hl_winrate(e.get('winrate_score'), wr_th, preflight.WINRATE_SCORE_MAX)}"
            t3_part = ""
            if e.get("winrate_t3_prob") is not None:
                t3_part = (
                    f"  T+3获利 {_hl_t3_prob(e.get('winrate_t3_prob'), e.get('winrate_t3_min'), cfg)}"
                )
            lines.append(f"    [{_hl(op_label, color)}] {_hl(e.get('code'), color)} {_hl(e.get('name'), color)} "
                         f"{_hl_price(q.get('price'), width=8)} {_hl_chg(q.get('chg_pct'), width=8)}"
                         f"  共振 {_hl_reso(e.get('total_score'), e.get('pass_threshold'), ev=e)}"
                         f"{wr_part}{t3_part}"
                         f"  {idn.get('tier')}{utils.num(idn.get('score'), 0)}"
                         f"  {(e.get('stage') or {}).get('stage') or '—'}"
                         f"  止损 {_hl_sl_pct(lv.get('sl_pct'))} 止盈 {_hl_tp_pct(lv.get('tp_pct'))}"
                         f"  R:R {_hl_odds(lv.get('odds'))}")
            if e.get("winrate_gate"):
                lines.append(f"        硬闸：{e['winrate_gate']}")
            if e.get("winrate_detail"):
                lines.append(f"        {e['winrate_detail']}")
            if e.get("winrate_t3_detail"):
                lines.append(f"        {e['winrate_t3_detail']}")
            shortfall = preflight.format_resonance_shortfall(e)
            if shortfall and key != "near_miss":
                lines.append(f"        {shortfall}")
            if e.get("triggers"):
                lines.append("        触发：" + "；".join(e["triggers"]))
            if key in ("buyable", "watch", "eve"):
                order_line = format_order_hint(e, style="console")
                if order_line:
                    lines.append(f"        {order_line}")
                t3_line = format_t3_expect_hint(e, style="console")
                if t3_line:
                    lines.append(f"        {t3_line}")
            if key == "near_miss" and e.get("reasons"):
                lines.append("        原因：" + "；".join(e["reasons"][:4]))
            tech = _tech_indicators_line(e)
            if tech:
                lines.append(f"        {tech}")

    # 候选明细：硬否决/数据缺不进上面四个桶，只能在这里看到为何被淘汰
    cands = result.get("candidates") or []
    cn = result.get("candidates_n", len(cands))
    lines.append(f"  ---- 候选明细（初筛 {cn} 只 → 淘汰/保留原因）----")
    if not cands:
        lines.append("    —")
    for c in cands:
        intr_s = _hl(f"{_intr(c.get('intraday')):>4}", utils.COLOR_INFO)
        lines.append(f"    [{_hl('候选', utils.COLOR_INFO)}] {_hl(c.get('code'), utils.COLOR_INFO)} {_hl(c.get('name') or '', utils.COLOR_INFO)} "
                     f"{_hl_chg(c.get('chg'), width=8)}  分时 {intr_s}"
                     f"  共振 {_hl_reso(c.get('score'), c.get('threshold'), max_score=c.get('scoring_max'))}"
                     f"  {_ident(c, 0):<8}"
                     f"  [{c.get('verdict') or '—'}]")
        lines.append(f"        原因：{cand_display_reason(c)}")
        order_line = format_order_hint(c, style="console")
        if order_line:
            lines.append(f"        {order_line}")
        t3_line = format_t3_expect_hint(c, style="console")
        if t3_line:
            lines.append(f"        {t3_line}")
        tech = _tech_indicators_line_cand(c)
        if tech:
            lines.append(f"        {tech}")

    for n in (result.get("notes") or []):
        lines.append(f"  · {n}")
    return "\n".join(lines)


def format_winrate_result(result: dict, cfg: Optional[Config] = None,
                          style: str = "console") -> str:
    """胜率选股摘要：操作建议与种子扫描一致（买入/观察），胜率分 X/7。

    style: console（ANSI）| plain（邮件纯文本）
    """
    cfg = cfg or load_config()
    threshold = int(result.get("winrate_threshold")
                    or cfg.get("winrate.buyable_threshold", 3))
    max_score = int(result.get("winrate_max") or preflight.WINRATE_SCORE_MAX)
    t3_min = float(cfg.get("winrate.t3_prob.min_prob", 0.35))
    lines = [
        "===== 胜率选股结果 =====",
        f"  时间 {result.get('at')}   门槛 ≥{threshold}/{max_score} 分（数据启发）",
        _score_legend_winrate(threshold, max_score, cfg),
    ]
    sectors = result.get("sectors") or []
    top = "、".join(f"{s.get('name')}(#{s.get('rank')})" for s in sectors[:3])
    lines.append(f"  板块 TOP：{top or '无'}")

    op_map = {
        "buyable": ("买入", utils.COLOR_SEED),
        "watch": ("观察", utils.COLOR_WARN),
    }
    buyable = result.get("buyable") or []
    watch_all = result.get("watch") or []
    watch_limit = int(result.get("watch_display_limit") or 3)
    watch_show = watch_all[:watch_limit] if watch_limit > 0 else watch_all

    for title, key, evs in (
        ("可买（影子，不写计划）", "buyable", buyable),
        ("观察", "watch", watch_show),
    ):
        op_label, color = op_map[key]
        total = len(watch_all) if key == "watch" else len(evs)
        suffix = ""
        if key == "watch" and watch_limit > 0 and total > len(evs):
            suffix = f"（展示 {len(evs)} / 共 {total}）"
        lines.append(f"  ---- {title}（{total}）{suffix} ----")
        if not evs:
            lines.append("    —" if key == "buyable" else "    无")
            continue
        for e in evs:
            q = e.get("quote") or {}
            idn = e.get("identity") or {}
            sec = e.get("sector") or {}
            sector_name = e.get("sector_name") or sec.get("name") or "—"
            reso_part = ""
            if e.get("total_score") is not None and e.get("pass_threshold") is not None:
                reso_part = (
                    f"  共振 {_hl_reso(e.get('total_score'), e.get('pass_threshold'), ev=e, style=style)}"
                )
            lines.append(
                f"    [{_sty(op_label, color, style)}] "
                f"{_sty(e.get('code') or '', color, style)} "
                f"{_sty(e.get('name') or '', color, style)} [{sector_name}]"
                f"  {_hl_price(q.get('price'), width=8, style=style)} "
                f"{_hl_chg(q.get('chg_pct'), width=8, style=style)}"
                f"  胜率 {_hl_winrate(e.get('winrate_score'), threshold, max_score, style=style)}"
                f"  T+3获利 {_hl_t3_prob(e.get('winrate_t3_prob'), e.get('winrate_t3_min'), cfg, style=style)}"
                f"{reso_part}"
                f"  {idn.get('tier') or '—'}{utils.num(idn.get('score'), 0)}"
                f"  {(e.get('stage') or {}).get('stage') or '—'}"
            )
            if e.get("winrate_gate"):
                lines.append(f"        硬闸：{e['winrate_gate']}")
            if e.get("winrate_detail"):
                lines.append(f"        {e['winrate_detail']}")
            if e.get("winrate_t3_detail"):
                lines.append(f"        {e['winrate_t3_detail']}")
            if e.get("triggers"):
                lines.append("        触发：" + "；".join(e["triggers"]))
            tech = _tech_indicators_line(e)
            if tech:
                lines.append(f"        {tech}")

    lines.append(
        "  注：操作建议与种子扫描一致（买入/观察）；影子可买只落盘对照，不写计划、不自动买入"
    )
    lines.append(
        "  读分：共振看纪律（六维满分9、门槛随档位/姿态浮动）；"
        "胜率看历史因子（满分7、门槛≥3）；"
        f"T+3获利看三日持有概率（综合板块/阶段/涨幅，门槛≥{t3_min:.0%}）；"
        "三者独立，须各自达标且无硬闸"
    )
    for n in (result.get("notes") or []):
        lines.append(f"  · {n}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 胜率邮件

def winrate_has_candidates(result: dict) -> bool:
    """胜率扫描是否有可通知候选（可买或观察，含硬闸降级）。"""
    return bool(result.get("buyable") or result.get("watch"))


def _winrate_email_state_path(cfg: Config) -> str:
    return cfg.data_file("winrate_email_state_file")


def _load_winrate_email_state(cfg: Config) -> dict:
    data = utils.read_json(_winrate_email_state_path(cfg), default={}) or {}
    data.setdefault("sent", [])
    return data


def _save_winrate_email_state(state: dict, cfg: Config) -> None:
    state["updated_at"] = utils.now().strftime("%Y-%m-%d %H:%M:%S")
    utils.write_json(_winrate_email_state_path(cfg), state)


def _winrate_email_dedupe_key(date: str, code: str) -> str:
    return f"{date}|{utils.norm_code(code)}"


def winrate_email_sent_codes(cfg: Optional[Config] = None,
                             date_str: Optional[str] = None) -> set:
    """当日已发过胜率邮件的股票代码集合。"""
    cfg = cfg or load_config()
    day = date_str or utils.today_str()
    codes: set = set()
    for rec in _load_winrate_email_state(cfg).get("sent", []):
        if rec.get("date") == day:
            code = utils.norm_code(rec.get("code") or "")
            if code:
                codes.add(code)
    return codes


def is_winrate_code_emailed_today(code: str, cfg: Optional[Config] = None,
                                  date_str: Optional[str] = None) -> bool:
    cfg = cfg or load_config()
    day = date_str or utils.today_str()
    key = _winrate_email_dedupe_key(day, code)
    return any(s.get("key") == key for s in _load_winrate_email_state(cfg).get("sent", []))


def filter_winrate_unsent(result: dict, cfg: Optional[Config] = None,
                          date_str: Optional[str] = None) -> dict:
    """过滤出当日尚未发过胜率邮件的候选（buyable/watch）。"""
    cfg = cfg or load_config()
    if not bool(cfg.get("winrate.email.dedupe_per_day", True)):
        return result
    sent = winrate_email_sent_codes(cfg, date_str)
    if not sent:
        return result

    def _new(evs: Optional[List[dict]]) -> List[dict]:
        out: List[dict] = []
        for ev in evs or []:
            code = utils.norm_code(ev.get("code") or "")
            if code and code not in sent:
                out.append(ev)
        return out

    filtered = dict(result)
    filtered["buyable"] = _new(result.get("buyable"))
    filtered["watch"] = _new(result.get("watch"))
    return filtered


def mark_winrate_email_sent(result: dict, cfg: Optional[Config] = None,
                            meta: Optional[dict] = None) -> None:
    """按股票标记当日已发（dedupe_per_day = 同 code 同日只发一封）。"""
    cfg = cfg or load_config()
    state = _load_winrate_email_state(cfg)
    day = utils.today_str()
    now = utils.now().strftime("%Y-%m-%d %H:%M")
    for ev in (result.get("buyable") or []) + (result.get("watch") or []):
        code = ev.get("code")
        if not code:
            continue
        key = _winrate_email_dedupe_key(day, code)
        if any(s.get("key") == key for s in state.get("sent", [])):
            continue
        rec = {
            "key": key,
            "date": day,
            "code": utils.norm_code(code),
            "name": ev.get("name"),
            "sent_at": now,
        }
        if meta:
            rec["meta"] = meta
        state.setdefault("sent", []).append(rec)
    state["last_date"] = day
    state["last_sent_at"] = now
    _save_winrate_email_state(state, cfg)


def _pass_status(ok: bool) -> str:
    return "达标" if ok else "未达标"


def _email_chg_text(chg_pct: Optional[float]) -> str:
    return "—" if chg_pct is None else f"{chg_pct:.2f}"


def compact_email_stock_line(ev: dict, op: str, cfg: Config,
                           wr_threshold: Optional[int] = None,
                           wr_max_score: Optional[int] = None) -> str:
    """单行邮件摘要（种子/胜率统一格式）。

    示例：【观察】603092 德力佳 涨幅7.05%,胜率2/7 ，门槛≥3（未达标）
          T+3 33%（未达标）共振4/8 未达标 （突破）
    """
    q = ev.get("quote") or {}
    code = ev.get("code") or "—"
    name = ev.get("name") or ""
    wr_th = int(wr_threshold or cfg.get("winrate.buyable_threshold", 3))
    wr_mx = int(wr_max_score or preflight.WINRATE_SCORE_MAX)

    line = f"【{op}】{code} {name}".strip()
    line += f" 涨幅{_email_chg_text(q.get('chg_pct'))}%"

    if ev.get("winrate_score") is not None:
        wr = int(ev["winrate_score"])
        mark = _pass_status(wr >= wr_th)
        line += f",胜率{wr}/{wr_mx} ，门槛≥{wr_th}（{mark}）"

    if ev.get("winrate_t3_prob") is not None:
        prob = float(ev["winrate_t3_prob"])
        th = float(ev.get("winrate_t3_min")
                   or cfg.get("winrate.t3_prob.min_prob", 0.35))
        mark = _pass_status(prob >= th)
        line += f" T+3 {prob:.0%}（{mark}）"

    if ev.get("total_score") is not None and ev.get("pass_threshold") is not None:
        sc = int(ev["total_score"])
        th = int(ev["pass_threshold"])
        mx = _reso_max(ev)
        mark = _pass_status(sc >= th)
        line += f"共振{sc}/{mx} {mark}"

    stage = (ev.get("stage") or {}).get("stage")
    if stage:
        line += f" （{stage}）"
    return line


def _email_summary_text(buyable: list, watch: list,
                        eve: Optional[list] = None,
                        shadow_buyable: bool = False,
                        empty_text: str = "今日无候选。") -> str:
    """邮件末尾一行总结。"""
    eve = eve or []
    if not buyable and not watch and not eve:
        return f"总结：{empty_text}"
    parts: List[str] = []
    if buyable:
        label = "可买(影子)" if shadow_buyable else "可买"
        parts.append(f"{label}{len(buyable)}只")
    if watch:
        parts.append(f"观察{len(watch)}只")
    if eve:
        parts.append(f"前夕{len(eve)}只")
    text = "、".join(parts)
    if buyable and not watch and not eve:
        return f"总结：{text}。"
    if watch and not buyable:
        return f"总结：{text}，暂无可买。"
    return f"总结：{text}。"


def format_winrate_email_digest(result: dict, cfg: Optional[Config] = None) -> str:
    """胜率邮件精简摘要：单行候选 + 总结；详情见 reports 或 winrate-scan。"""
    cfg = cfg or load_config()
    threshold = int(result.get("winrate_threshold")
                    or cfg.get("winrate.buyable_threshold", 3))
    max_score = int(result.get("winrate_max") or preflight.WINRATE_SCORE_MAX)
    at = result.get("at") or utils.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"TEA 胜率选股  {at}"]

    buyable = result.get("buyable") or []
    watch = result.get("watch") or []
    for ev in buyable:
        lines.append(compact_email_stock_line(ev, "买入", cfg, threshold, max_score))
    for ev in watch:
        lines.append(compact_email_stock_line(ev, "观察", cfg, threshold, max_score))

    lines.append(_email_summary_text(buyable, watch, shadow_buyable=True))
    return "\n".join(lines)


def winrate_email_subject(result: dict, cfg: Optional[Config] = None) -> str:
    """邮件主题（不含前缀）。"""
    cfg = cfg or load_config()
    buy_n = len(result.get("buyable") or [])
    watch_n = len(result.get("watch") or [])
    parts: List[str] = []
    if buy_n:
        parts.append(f"影子可买{buy_n}只")
    if watch_n:
        parts.append(f"观察{watch_n}只")
    headline = " ".join(parts) if parts else "有候选"
    codes: List[str] = []
    for ev in (result.get("buyable") or []) + (result.get("watch") or [])[:3]:
        code = ev.get("code")
        name = ev.get("name") or ""
        if code:
            codes.append(f"{code}{name[:4]}" if name else code)
    suffix = " ".join(codes[:3])
    if suffix:
        return f"胜率选股 {headline} · {suffix}"
    return f"胜率选股 {headline}"


def winrate_email_body(result: dict, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    return format_winrate_email_digest(result, cfg)


def send_winrate_email(result: dict, cfg: Optional[Config] = None,
                       force: bool = False,
                       sender: Optional[Any] = None) -> Dict[str, Any]:
    """胜率选股有候选时发邮件。返回 {ok, skip?, error?, subject?}。"""
    cfg = cfg or load_config()
    out: Dict[str, Any] = {"date": utils.today_str()}

    if not winrate_has_candidates(result):
        out["skip"] = "empty"
        return out

    if not force and not cfg.get("winrate.email.enabled", True):
        out["skip"] = "winrate_email_disabled"
        return out

    if not notify.smtp_ready(cfg):
        out["skip"] = "email_not_configured"
        return out

    today = utils.today_str()
    notify_result = filter_winrate_unsent(result, cfg, today) if not force else result
    if not winrate_has_candidates(notify_result):
        if winrate_has_candidates(result):
            out["skip"] = "all_codes_already_sent"
            out["deduped"] = sorted(winrate_email_sent_codes(cfg, today))
        else:
            out["skip"] = "empty"
        return out

    subject = winrate_email_subject(notify_result, cfg)
    body = winrate_email_body(notify_result, cfg)
    prefix = str(cfg.get("winrate.email.subject_prefix") or "[TEA胜率]")
    res = notify.send_email(cfg, subject=subject, body=body,
                            subject_prefix=prefix, sender=sender)
    out.update(res)
    out["subject"] = subject
    out["notified_codes"] = [
        utils.norm_code(ev.get("code") or "")
        for ev in (notify_result.get("buyable") or []) + (notify_result.get("watch") or [])
        if ev.get("code")
    ]
    if res.get("ok"):
        mark_winrate_email_sent(notify_result, cfg, meta={"subject": subject})
    return out


# ------------------------------------------------------------------ 种子邮件

_VERDICT_EMAIL = {
    VERDICT_TRADEABLE: "有可买",
    VERDICT_PENDING: "仅观察",
    VERDICT_EMPTY: "空仓",
}


def seed_should_email(result: dict, cfg: Optional[Config] = None) -> bool:
    """种子扫描是否应发邮件（send_on_empty=false 时仅 buyable/watch/eve 非空发）。"""
    cfg = cfg or load_config()
    if bool(cfg.get("seed.email.send_on_empty", True)):
        return True
    return bool(result.get("buyable") or result.get("watch") or result.get("eve"))


def _seed_email_state_path(cfg: Config) -> str:
    return cfg.data_file("seed_email_state_file")


def _load_seed_email_state(cfg: Config) -> dict:
    return utils.read_json(_seed_email_state_path(cfg), default={}) or {}


def _save_seed_email_state(state: dict, cfg: Config) -> None:
    state["updated_at"] = utils.now().strftime("%Y-%m-%d %H:%M:%S")
    utils.write_json(_seed_email_state_path(cfg), state)


def is_seed_email_sent_today(cfg: Optional[Config] = None,
                             date_str: Optional[str] = None) -> bool:
    cfg = cfg or load_config()
    day = date_str or utils.today_str()
    return (_load_seed_email_state(cfg).get("last_date") or "") == day


def mark_seed_email_sent(cfg: Optional[Config] = None, meta: Optional[dict] = None) -> None:
    cfg = cfg or load_config()
    day = utils.today_str()
    state = _load_seed_email_state(cfg)
    state["last_date"] = day
    state["last_sent_at"] = utils.now().strftime("%Y-%m-%d %H:%M")
    if meta:
        state["last_meta"] = meta
    _save_seed_email_state(state, cfg)


def format_seed_email_digest(result: dict, cfg: Optional[Config] = None) -> str:
    """种子邮件精简摘要：单行候选 + 总结；详情见 SEED 报告或 seed-show。"""
    cfg = cfg or load_config()
    at = result.get("at") or utils.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"TEA 种子扫描  {at}"]

    buyable = result.get("buyable") or []
    watch = result.get("watch") or []
    eve = result.get("eve") or []
    for ev in buyable:
        lines.append(compact_email_stock_line(ev, "买入", cfg))
    for ev in watch:
        lines.append(compact_email_stock_line(ev, "观察", cfg))
    for ev in eve:
        lines.append(compact_email_stock_line(ev, "观察", cfg))

    if not buyable and not watch and not eve:
        lines.append(_email_summary_text([], [], empty_text="今日无可买、无观察标的。"))
    else:
        lines.append(_email_summary_text(buyable, watch, eve=eve))
    return "\n".join(lines)


def seed_email_subject(result: dict, cfg: Optional[Config] = None) -> str:
    """种子邮件主题（不含前缀）。"""
    buy_n = len(result.get("buyable") or [])
    watch_n = len(result.get("watch") or [])
    eve_n = len(result.get("eve") or [])
    parts: List[str] = []
    if buy_n:
        parts.append(f"可买{buy_n}只")
    if watch_n:
        parts.append(f"观察{watch_n}只")
    if eve_n:
        parts.append(f"前夕{eve_n}只")
    headline = " ".join(parts) if parts else _VERDICT_EMAIL.get(
        result.get("verdict", VERDICT_EMPTY), "空仓")
    codes: List[str] = []
    for ev in (result.get("buyable") or []) + (result.get("watch") or [])[:3]:
        code = ev.get("code")
        name = ev.get("name") or ""
        if code:
            codes.append(f"{code}{name[:4]}" if name else code)
    suffix = " ".join(codes[:3])
    if suffix:
        return f"种子扫描 {headline} · {suffix}"
    return f"种子扫描 {headline}"


def seed_email_body(result: dict, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    return format_seed_email_digest(result, cfg)


def send_seed_email(result: dict, cfg: Optional[Config] = None,
                    force: bool = False,
                    sender: Optional[Any] = None) -> Dict[str, Any]:
    """种子扫描完成后发邮件。返回 {ok, skip?, error?, subject?}。"""
    cfg = cfg or load_config()
    out: Dict[str, Any] = {"date": utils.today_str()}

    if not seed_should_email(result, cfg):
        out["skip"] = "empty"
        return out

    if not force and not cfg.get("seed.email.enabled", True):
        out["skip"] = "seed_email_disabled"
        return out

    if not notify.smtp_ready(cfg):
        out["skip"] = "email_not_configured"
        return out

    today = utils.today_str()
    if not force and bool(cfg.get("seed.email.dedupe_per_day", True)):
        if is_seed_email_sent_today(cfg, today):
            out["skip"] = "already_sent"
            return out

    subject = seed_email_subject(result, cfg)
    body = seed_email_body(result, cfg)
    prefix = str(cfg.get("seed.email.subject_prefix") or "[TEA种子]")
    res = notify.send_email(cfg, subject=subject, body=body,
                            subject_prefix=prefix, sender=sender)
    out.update(res)
    out["subject"] = subject
    if res.get("ok"):
        mark_seed_email_sent(cfg, meta={"subject": subject, "verdict": result.get("verdict")})
    return out


def format_detail(result: dict, index: int = 1) -> str:
    """展开可买清单中第 index 只的完整评估。"""
    evs = (result.get("buyable") or []) + (result.get("watch") or [])
    if not evs or index < 1 or index > len(evs):
        return "无此序号"
    return preflight.format_evaluation(evs[index - 1])


def summarize(result: dict) -> Dict[str, Any]:
    """精简结构，供 accumulator / 日志留存。"""
    pick = lambda evs: [{"code": e.get("code"), "name": e.get("name"),
                         "score": e.get("total_score"), "threshold": e.get("pass_threshold"),
                         "tier": (e.get("identity") or {}).get("tier"),
                         "stage": (e.get("stage") or {}).get("stage"),
                         "track": e.get("track")} for e in (evs or [])]
    return {
        "date": utils.today_str(), "at": result.get("at"), "scan_id": result.get("scan_id"),
        "scan_anchor": result.get("scan_anchor"),
        "verdict": result.get("verdict"), "tier": result.get("tier"),
        "sentiment_score": (result.get("sentiment") or {}).get("score"),
        "cycle": (result.get("sentiment") or {}).get("cycle"),
        "stance": (result.get("sentiment") or {}).get("stance"),
        "sectors": [{"rank": s.get("rank"), "name": s.get("name"), "chg": s.get("chg"),
                     "limit_up_count": s.get("limit_up_count"),
                     "total_score": s.get("total_score")} for s in (result.get("sectors") or [])],
        "candidates_n": result.get("candidates_n", 0),
        "veto_passed_n": result.get("veto_passed_n", 0),
        "buyable": pick(result.get("buyable")), "watch": pick(result.get("watch")),
        "near_miss": pick(result.get("near_miss")), "eve": pick(result.get("eve")),
        "notes": result.get("notes") or [],
    }
