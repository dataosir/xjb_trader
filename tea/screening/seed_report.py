"""种子扫描报告生成：SEED_*.md（Markdown 存档）+ 控制台摘要。

verdict：HAS_TRADEABLE（有可买）/ PENDING（仅观察）/ EMPTY（主动不开新仓）。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from tea.config.config_store import Config, load_config
from tea.analysis.sentiment import allow_new_label
from tea.core import utils
from tea.data.indicators import format_bollinger
from . import preflight
from .screener import (VERDICT_EMPTY, VERDICT_PENDING, VERDICT_TRADEABLE,
                       dyn_window_text)


def _hl(text: str, color: str = "") -> str:
    """ANSI 高亮（复用 utils.hl，默认警告黄）。"""
    return utils.hl(text, color or utils.COLOR_WARN)

VERDICT_LABEL = {
    VERDICT_TRADEABLE: "HAS_TRADEABLE（有可买标的，已写次日计划）",
    VERDICT_PENDING: "PENDING（无可买，仅观察轨跟踪）",
    VERDICT_EMPTY: "EMPTY（主动不开新仓 — 宁缺毋滥）",
}


# ------------------------------------------------------------------ 小工具

def _sent_line(sent: Optional[dict]) -> str:
    if not sent:
        return "情绪数据缺失"
    return (f"情绪 {utils.num(sent.get('score'), 1)} 分 · {sent.get('cycle')} · "
            f"姿态 {sent.get('stance')} · 半仓基数 ×{utils.num(sent.get('base_pos_mult'), 2)}"
            + ("（冰点降仓）" if sent.get("ice_cut") else "")
            + f" · 新开 {allow_new_label(sent)}")


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
    return (f"| {ev.get('code')} | {ev.get('name')} | {ev.get('sector_name') or (ev.get('sector') or {}).get('name') or '—'} "
            f"| {utils.num(q.get('price'))} | {utils.pct(q.get('chg_pct'))} "
            f"| {ev.get('total_score')}/{ev.get('pass_threshold')} "
            f"| {idn.get('tier')} {utils.num(idn.get('score'), 1)} "
            f"| {(ev.get('stage') or {}).get('stage') or '—'} "
            f"| {utils.pct(lv.get('sl_pct'))} / {utils.pct(lv.get('tp_pct'))} "
            f"| {utils.num(lv.get('odds'))} "
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
    return "—" if c.get("score") is None else f"{c.get('score')}/{c.get('threshold')}"


def _ident(c: dict, nd: int = 1) -> str:
    if c.get("identity_tier") is None and c.get("identity_score") is None:
        return "—"
    return f"{c.get('identity_tier') or '—'} {utils.num(c.get('identity_score'), nd)}"


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
    return (f"| {c.get('code')} | {c.get('name')} | {c.get('sector_name') or '—'} "
            f"| {utils.pct(c.get('chg'))} | {_intr(c.get('intraday'))} | {_reso(c)} "
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
            lines.append(f"| {s.get('rank')} | {s.get('name')} | {utils.pct(s.get('chg'))} "
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
            tech = _tech_indicators_line_cand(c)
            if tech:
                lines.append(f"- **{c.get('code')} {c.get('name')}** — 技术观测：{tech}")
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


# ------------------------------------------------------------------ 控制台

def format_result(result: dict, cfg: Optional[Config] = None) -> str:
    """控制台摘要（不含 Markdown 表格）。"""
    cfg = cfg or load_config()
    verdict = result.get("verdict", VERDICT_EMPTY)
    lines = [
        "===== 种子扫描结果 =====",
        f"  时间 {result.get('at')}   裁决 {VERDICT_LABEL.get(verdict, verdict)}",
        f"  {_sent_line(result.get('sentiment'))}",
        f"  启用档位 {result.get('tier') or '—'}"
        f"   漏斗 初筛 {result.get('candidates_n', 0)} → VETO 过 {result.get('veto_passed_n', 0)}"
        f" → 可买 {len(result.get('buyable') or [])}",
    ]
    dyn = _dyn_line(result)
    if dyn:
        lines.append(f"  {dyn}")

    sectors = result.get("sectors") or []
    lines.append(f"  ---- 第1步 板块 TOP{len(sectors)} ----")
    if sectors:
        for s in sectors:
            lines.append(f"    #{s.get('rank'):<3} {s.get('name'):<10} {utils.pct(s.get('chg')):>8}"
                         f"  涨停 {s.get('limit_up_count')} 家  温和票 {s.get('mild_n')} 只"
                         f"  综合 {utils.num(s.get('total_score'), 1)}  [{s.get('gate') or '—'}]")
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
            lines.append(f"    [{_hl(op_label, color)}] {_hl(e.get('code'), color)} {_hl(e.get('name'), color)} "
                         f"{utils.num(q.get('price')):>8} {utils.pct(q.get('chg_pct')):>8}"
                         f"  共振 {e.get('total_score')}/{e.get('pass_threshold')}"
                         f"  {idn.get('tier')}{utils.num(idn.get('score'), 0)}"
                         f"  {(e.get('stage') or {}).get('stage') or '—'}"
                         f"  止损 {utils.pct(lv.get('sl_pct'))} 止盈 {utils.pct(lv.get('tp_pct'))}"
                         f"  R:R {utils.num(lv.get('odds'))}")
            shortfall = preflight.format_resonance_shortfall(e)
            if shortfall and key != "near_miss":
                lines.append(f"        {shortfall}")
            if e.get("triggers"):
                lines.append("        触发：" + "；".join(e["triggers"]))
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
        lines.append(f"    [{_hl('候选', utils.COLOR_INFO)}] {_hl(c.get('code'), utils.COLOR_INFO)} {_hl(c.get('name') or '', utils.COLOR_INFO)} "
                     f"{utils.pct(c.get('chg')):>8}  分时 {_intr(c.get('intraday')):>4}"
                     f"  共振 {_reso(c):<5}  {_ident(c, 0):<8}"
                     f"  [{c.get('verdict') or '—'}]")
        lines.append(f"        原因：{cand_display_reason(c)}")
        tech = _tech_indicators_line_cand(c)
        if tech:
            lines.append(f"        {tech}")

    for n in (result.get("notes") or []):
        lines.append(f"  · {n}")
    return "\n".join(lines)


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
