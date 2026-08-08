"""周报生成：把一周的天气、扫描漏斗、落选原因、交易绩效、跟涨经验汇成 WEEKLY_*.md。

周报的用途不是"晒收益"，而是回答：
- 这周纪律执行得怎么样（有多少次绕过门禁 / FORCE）
- 空仓日是被哪一步卡住的（落选原因累计）
- 哪个评分档/身份/阶段在赚钱（是否该调参）
"""
from __future__ import annotations

import glob
import os
from typing import Any, Dict, List, Optional

from tea.analysis import followthrough, stats
from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.portfolio import accumulator, trades as trades_mod, watch_pool


def collect(days: int = 7, cfg: Optional[Config] = None) -> Dict[str, Any]:
    """汇总一周数据。"""
    cfg = cfg or load_config()
    rd = accumulator.range_digest(days, cfg)
    ds = rd.get("days") or []
    since = ds[0] if ds else utils.today_str()

    week_trades = [t for t in trades_mod.effective_trades(cfg)
                   if (t.get("closed_date") or "") >= since]
    forced = [r for r in accumulator.load_log(cfg, kind=accumulator.KIND_EVAL, since=since)
              if r.get("note") and "FORCE" in str(r.get("note")).upper()]
    evals = accumulator.load_log(cfg, kind=accumulator.KIND_EVAL, since=since)
    decisions: Dict[str, int] = {}
    for e in evals:
        d = e.get("decision") or "?"
        decisions[d] = decisions.get(d, 0) + 1

    return {
        "since": since, "until": ds[-1] if ds else utils.today_str(),
        "days": ds, "range": rd,
        "week_perf": stats.perf(week_trades),
        "week_trades": week_trades,
        "overall": stats.overall(cfg),
        "decisions": decisions,
        "forced_n": len(forced),
        "followthrough": followthrough.aggregate(cfg),
        "watch_items": watch_pool.items(cfg),
        "empty_days": (rd.get("verdicts") or {}).get("EMPTY", 0)
                      + (rd.get("verdicts") or {}).get("NO_SCAN", 0),
    }


# ------------------------------------------------------------------ 纪律自查

def check_discipline(wk: dict, cfg: Optional[Config] = None) -> List[dict]:
    """纪律自查项：给出结论 + 建议，不做自动调参。"""
    cfg = cfg or load_config()
    out: List[dict] = []
    rd = wk.get("range") or {}
    n_days = len(wk.get("days") or [])
    wp = wk.get("week_perf") or {}

    out.append({
        "item": "FORCE 使用",
        "value": f"{wk.get('forced_n', 0)} 次",
        "ok": wk.get("forced_n", 0) == 0,
        "advice": "零 FORCE 是纪律满分" if not wk.get("forced_n")
        else "每次 FORCE 都要在复盘里写清理由，连续 FORCE 说明门槛设错了",
    })

    empty = wk.get("empty_days", 0)
    out.append({
        "item": "空仓日占比",
        "value": f"{empty}/{n_days} 天",
        "ok": True,
        "advice": "空仓是常态，宁缺毋滥" if (n_days and empty / n_days >= 0.5)
        else "出手频率偏高，检查是否放宽了涨幅窗",
    })

    total_buy = rd.get("total_buyable") or 0
    out.append({
        "item": "扫描产出",
        "value": f"评估 {rd.get('total_evaluations') or 0} 次 → 可买 {total_buy} 只",
        "ok": True,
        "advice": "四步流漏斗见下方落选原因累计",
    })

    if wp.get("n"):
        wr = wp.get("win_rate")
        out.append({
            "item": "本周胜率",
            "value": f"{wr * 100:.1f}%（{wp['n']} 笔）" if wr is not None else f"{wp['n']} 笔",
            "ok": bool(wr is not None and wr >= 0.4),
            "advice": "样本 <10 笔不足以调参，先累积" if wp["n"] < 10
            else ("胜率达标，可维持当前门槛" if (wr or 0) >= 0.4
                  else "胜率偏低：优先收紧身份门槛与 R:R，而不是放宽止损"),
        })
        out.append({
            "item": "平均 R",
            "value": utils.num(wp.get("avg_r")),
            "ok": bool((wp.get("avg_r") or 0) > 0),
            "advice": "正 R 期望，继续执行" if (wp.get("avg_r") or 0) > 0
            else "负 R 期望：检查是否有破位不止损（R < -1 的单）",
        })
        bad = [t for t in wk.get("week_trades") or []
               if t.get("r_multiple") is not None and t["r_multiple"] < -1.2]
        out.append({
            "item": "止损纪律",
            "value": f"超额亏损 {len(bad)} 笔（R < -1.2）",
            "ok": not bad,
            "advice": "止损执行到位" if not bad
            else "存在扛单：" + "、".join(f"{t.get('code')}({utils.num(t.get('r_multiple'))}R)" for t in bad),
        })

    sc = rd.get("avg_sentiment")
    if sc is not None:
        out.append({
            "item": "周均情绪",
            "value": utils.num(sc, 1),
            "ok": True,
            "advice": "冰点周（<40）本就该空仓，不必自责" if sc < 40
            else ("高潮周（>70）注意别追高" if sc > 70 else "情绪中性区，正常执行"),
        })

    over = len(wk.get("watch_items") or [])
    max_size = int(cfg.get("watch.max_size", 12))
    out.append({
        "item": "观察池",
        "value": f"{over}/{max_size}",
        "ok": over <= max_size,
        "advice": "记得跑收盘复核剔除超时项" if over else "观察池为空",
    })
    return out


# ------------------------------------------------------------------ Markdown

def render_md(wk: Optional[dict] = None, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    wk = wk or collect(7, cfg)
    rd = wk.get("range") or {}
    wp = wk.get("week_perf") or {}
    lines: List[str] = [
        f"# WEEKLY {wk.get('since')} ~ {wk.get('until')}",
        "",
        f"- 覆盖交易日：{len(wk.get('days') or [])} 天",
        f"- 周均情绪：{utils.num(rd.get('avg_sentiment'), 1)}",
        "- 裁决分布：" + ("　".join(f"{k} {v} 天" for k, v in (rd.get("verdicts") or {}).items()) or "—"),
        f"- 本周交易 {wp.get('n', 0)} 笔，盈亏 {utils.money(wp.get('pnl'))}，"
        f"胜率 {stats.fmt_wr(wp.get('win_rate'))}，均 R {utils.num(wp.get('avg_r'))}",
        "- 决策分布：" + ("　".join(f"{k} {v}" for k, v in (wk.get("decisions") or {}).items()) or "—")
        + f"　FORCE {wk.get('forced_n', 0)} 次",
        "",
    ]

    # ---- 纪律自查
    lines += ["## 纪律自查", "", "| 项目 | 实测 | 判定 | 建议 |", "| --- | --- | --- | --- |"]
    for c in check_discipline(wk, cfg):
        lines.append(f"| {c['item']} | {c['value']} | {'✅' if c['ok'] else '⚠️'} | {c['advice']} |")
    lines.append("")

    # ---- 每日
    lines += ["## 每日流水", "",
              "| 日期 | 情绪 | 周期 | 姿态 | 裁决 | 档位 | 初筛 | VETO过 | 可买 | 观察 | 评估 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for d in rd.get("digests") or []:
        lines.append(f"| {d.get('date')} | {utils.num(d.get('sentiment_score'), 1)} "
                     f"| {d.get('cycle') or '—'} | {d.get('stance') or '—'} "
                     f"| {d.get('verdict') or 'NO_SCAN'} | {d.get('tier') or '—'} "
                     f"| {d.get('candidates_n') or 0} | {d.get('veto_passed_n') or 0} "
                     f"| {d.get('buyable_n') or 0} | {d.get('watch_n') or 0} "
                     f"| {d.get('evaluations') or 0} |")
    if not rd.get("digests"):
        lines.append("| — | | | | | | | | | | |")
    lines.append("")

    # ---- 落选原因
    reasons = rd.get("reasons") or {}
    lines += ["## 落选原因累计（为什么没票）", ""]
    if reasons:
        lines += ["| 原因 | 次数 |", "| --- | --- |"]
        lines += [f"| {k} | {v} |" for k, v in list(reasons.items())[:15]]
    else:
        lines.append("本周无落选记录。")
    lines.append("")

    # ---- 本周成交
    lines += ["## 本周成交", ""]
    if wk.get("week_trades"):
        lines += ["| 平仓日 | 代码 | 名称 | 买入 | 卖出 | 涨跌 | R | 盈亏 | 共振 | 身份 | 原因 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for t in wk["week_trades"]:
            lines.append(f"| {t.get('closed_date')} | {t.get('code')} | {t.get('name')} "
                         f"| {utils.num(t.get('entry'))} | {utils.num(t.get('exit'))} "
                         f"| {utils.pct(t.get('pnl_pct'))} | {utils.num(t.get('r_multiple'))} "
                         f"| {utils.money(t.get('pnl'))} | {t.get('total_score') or '—'} "
                         f"| {t.get('identity_tier') or '—'} | {t.get('reason') or ''} |")
    else:
        lines.append("本周无平仓记录。")
    lines.append("")

    # ---- 累计统计与归因
    lines.append(stats.render_md(wk.get("overall"), cfg))

    # ---- 跟涨经验
    agg = wk.get("followthrough") or {}
    lines += ["## 跟涨经验（阶段×档位×轨道 T+1 胜率）", ""]
    if agg:
        min_n = int(cfg.get("followthrough.min_samples", 8))
        lines += ["| 分组 | 样本 | T+1 胜率 | 有效 |", "| --- | --- | --- | --- |"]
        for k, v in sorted(agg.items(), key=lambda kv: -(kv[1].get("n") or 0)):
            n = v.get("n") or 0
            lines.append(f"| {k} | {n} | {stats.fmt_wr(v.get('rate'))} "
                         f"| {'是' if n >= min_n else f'否（需≥{min_n}）'} |")
    else:
        lines.append("暂无种子跟踪样本（需先跑几天 seed-plan 并回填 T+1 结果）。")
    lines.append("")

    # ---- 观察池
    its = wk.get("watch_items") or []
    lines += [f"## 观察池现状（{len(its)}/{cfg.get('watch.max_size', 12)}）", ""]
    if its:
        lines += ["| 轨道 | 代码 | 名称 | 入池 | 参考价 | 共振 | 身份 | 状态 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for i in its:
            lines.append(f"| {i.get('track')} | {i.get('code')} | {i.get('name')} "
                         f"| {i.get('added_date')} | {utils.num(i.get('ref_price'))} "
                         f"| {i.get('total_score')}/{i.get('pass_threshold')} "
                         f"| {i.get('identity_tier')} | {i.get('status')} |")
    else:
        lines.append("观察池为空。")
    lines.append("")

    # ---- 后验分析入口（检查本周扫描详细日志是否存在）
    scan_files = glob.glob(os.path.join(cfg.data_dir(), "scan_details_*.json"))
    lines.append("## 🔍 后验分析")
    if scan_files:
        lines.append(
            f"本周疑似生成了 {len(scan_files)} 个扫描详细日志文件，可运行后验分析查看被否决但后来大涨的股票："
        )
        lines.append("```bash")
        lines.append("python -m tea.reporting.retrospective")
        lines.append("```")
        lines.append("报告将保存为 `reports/retrospective_{today}.md`，包含 D+3/5/10 收益率、遗漏关注等表格。")
    else:
        lines.append(
            "本周没有扫描详细日志，无法进行后验分析。请确保每日执行种子扫描（菜单 5）以生成日志。"
        )
    lines.append("")

    lines += ["---", "",
              f"> 生成于 {utils.now().strftime('%Y-%m-%d %H:%M:%S')}　"
              "调参提醒：样本 <10 笔的归因结论不可信，先累积再改配置。"]
    return "\n".join(lines)


def write_report(days: int = 7, cfg: Optional[Config] = None) -> str:
    """生成并落盘 WEEKLY_<stamp>.md。"""
    cfg = cfg or load_config()
    wk = collect(days, cfg)
    prefix = cfg.get("report.weekly_prefix", "WEEKLY")
    path = cfg.report_file(f"{prefix}_{utils.stamp()}.md")
    out = utils.atomic_write(path, render_md(wk, cfg))
    utils.cleanup_reports(cfg)
    return out


# ------------------------------------------------------------------ 控制台

def format_weekly(wk: Optional[dict] = None, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    wk = wk or collect(7, cfg)
    rd = wk.get("range") or {}
    wp = wk.get("week_perf") or {}
    lines = [
        f"===== 周报 {wk.get('since')} ~ {wk.get('until')}（{len(wk.get('days') or [])} 个交易日）=====",
        f"  周均情绪 {utils.num(rd.get('avg_sentiment'), 1)}"
        f"   裁决 " + ("  ".join(f"{k} {v}天" for k, v in (rd.get("verdicts") or {}).items()) or "—"),
        f"  本周交易 {wp.get('n', 0)} 笔  盈亏 {utils.money(wp.get('pnl'))}"
        f"  胜率 {stats.fmt_wr(wp.get('win_rate'))}  均R {utils.num(wp.get('avg_r'))}"
        f"  FORCE {wk.get('forced_n', 0)} 次",
        "  ---- 纪律自查 ----",
    ]
    for c in check_discipline(wk, cfg):
        lines.append(f"    {'✓' if c['ok'] else '!'} {c['item']}：{c['value']} — {c['advice']}")
    lines.append("  ---- 每日 ----")
    for d in rd.get("digests") or []:
        lines.append(f"    {d.get('date')}  情绪 {utils.num(d.get('sentiment_score'), 1):>5}"
                     f"  {str(d.get('stance') or '—'):<4}  {str(d.get('verdict') or 'NO_SCAN'):<14}"
                     f"  初筛 {d.get('candidates_n') or 0:>3} → 可买 {d.get('buyable_n') or 0}")
    if not rd.get("digests"):
        lines.append("    暂无记录")
    reasons = rd.get("reasons") or {}
    if reasons:
        lines.append("  ---- 落选原因累计 TOP ----")
        for k, v in list(reasons.items())[:10]:
            lines.append(f"    {v:>4}  {k}")
    # 控制台末尾同样提示后验分析
    scan_files = glob.glob(os.path.join(cfg.data_dir(), "scan_details_*.json"))
    if scan_files:
        lines.append(f"  ⏳ 本周有 {len(scan_files)} 个扫描日志，可运行 `python -m tea.reporting.retrospective` 查看遗漏")
    return "\n".join(lines)
