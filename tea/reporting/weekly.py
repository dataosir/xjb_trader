"""周报生成：把一周的天气、扫描漏斗、落选原因、交易绩效、跟涨经验汇成 WEEKLY_*.md。

周报的用途不是"晒收益"，而是回答：
- 这周纪律执行得怎么样（有多少次绕过门禁 / FORCE）
- 空仓日是被哪一步卡住的（落选原因累计）
- 哪个评分档/身份/阶段在赚钱（是否该调参）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tea.analysis import followthrough, stats
from tea.config.config_store import Config, load_config
from tea.core import notify, utils
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
        "t3_attribution": followthrough.t3_attribution(cfg),
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

    t3 = wk.get("t3_attribution") or {}
    gap = t3.get("gap") or {}
    pending_t3 = gap.get("pending_t3", 0)
    total_n = t3.get("total_n") or 0
    total_rate = t3.get("total_rate")
    mengya_rate = t3.get("mengya_rate")
    mengya_n = t3.get("mengya_n") or 0
    t3_val = (f"全样本 {total_rate * 100:.0f}%（{t3.get('total_up', 0)}/{total_n}）"
              if total_rate is not None else "尚无 T+3 回填")
    if mengya_n and mengya_rate is not None:
        t3_val += f"；萌芽 {mengya_rate * 100:.0f}%（{t3.get('mengya_up', 0)}/{mengya_n}）"
    out.append({
        "item": "T+3 上涨率（方案 E）",
        "value": t3_val,
        "ok": pending_t3 == 0,
        "advice": (f"待 T+3 回填 {pending_t3} 条，周五周报前会自动 review"
                   if pending_t3 else "T+3 已齐，看周报「三日持有」段对照 rank≤3 / 影子桶"),
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

    # ---- T+3 三日持有（方案 E：突出萌芽 / rank / 影子桶）
    t3 = wk.get("t3_attribution") or {}
    lines += ["## 三日持有 T+3>0（方案 E，只读对照）", ""]
    if not t3.get("total_n"):
        lines.append("尚无 T+3 回填样本；工作日 15:35 自动 review 或菜单 `8` 手动复核。")
    else:
        tr = t3.get("total_rate") or 0.0
        lines.append(f"- **全样本**：{t3.get('total_up', 0)}/{t3['total_n']} = **{tr:.0%}**")
        if t3.get("mengya_n"):
            mr = t3.get("mengya_rate") or 0.0
            lines.append(f"- **萌芽专看**（方案 E）：{t3.get('mengya_up', 0)}/{t3['mengya_n']} = **{mr:.0%}**")
        gap = t3.get("gap") or {}
        lines.append(f"- 待回填：T+1 {gap.get('pending_t1', 0)}｜T+3 {gap.get('pending_t3', 0)}")
        for dim, title in (("rank", "板块排名"), ("chg", "涨幅桶"), ("stage", "阶段"), ("track", "轨道")):
            bucket = t3.get(dim) or {}
            if not bucket:
                continue
            lines += ["", f"### {title}", "", "| 分组 | 样本 | T+3>0 | 胜率 |", "| --- | --- | --- | --- |"]
            for k, v in sorted(bucket.items(), key=lambda x: -x[1]["n"]):
                rr = v.get("rate")
                lines.append(f"| {k} | {v['n']} | {v['up']} | {stats.fmt_wr(rr)} |")
        shadow = t3.get("shadow") or {}
        if shadow.get("n_t3"):
            sr = shadow.get("t3_up_rate") or 0.0
            flag = "✅" if shadow.get("ready") else "对照中"
            lines += ["", f"### 影子桶（萌芽∪前三非突破） {flag}",
                      f"- T+3>0：{shadow.get('t3_up', 0)}/{shadow['n_t3']} = **{sr:.0%}**"
                      f"（门槛 ≥{shadow.get('target', 0.6):.0%}，n≥{shadow.get('min_samples', 15)}）"]
    lines.append("")

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


# ------------------------------------------------------------------ 周报邮件（F17）

def _state_path(cfg: Config) -> str:
    return cfg.data_file("weekly_email_state_file")


def _load_state(cfg: Config) -> dict:
    return utils.read_json(_state_path(cfg), default={}) or {}


def _save_state(state: dict, cfg: Config) -> None:
    utils.write_json(_state_path(cfg), state)


def iso_week_key(d: Optional[Any] = None) -> str:
    """ISO 年-周键，用于去重（如 2026-W36）。"""
    dt = d if hasattr(d, "isocalendar") else utils.now().date()
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def is_sent_this_week(cfg: Optional[Config] = None, week_key: Optional[str] = None) -> bool:
    """本周是否已发送周报邮件。"""
    cfg = cfg or load_config()
    key = week_key or iso_week_key()
    return (_load_state(cfg).get("last_week") or "") == key


def mark_sent(cfg: Optional[Config] = None, week_key: Optional[str] = None,
              meta: Optional[dict] = None) -> None:
    """记录本周已发送。"""
    cfg = cfg or load_config()
    key = week_key or iso_week_key()
    state = _load_state(cfg)
    state["last_week"] = key
    state["last_sent_date"] = utils.today_str()
    if meta:
        state["last_meta"] = meta
    _save_state(state, cfg)


def email_subject(wk: dict, cfg: Optional[Config] = None) -> str:
    """邮件主题（不含前缀，前缀由 notify.send_email 拼接）。"""
    return f"选股周报 {wk.get('since')} ~ {wk.get('until')}"


def email_body(wk: dict, cfg: Optional[Config] = None) -> str:
    """邮件正文：摘要 + 完整 Markdown 周报。"""
    cfg = cfg or load_config()
    summary = format_weekly(wk, cfg)
    md = render_md(wk, cfg)
    return (
        "以下为 TEA 当周选股复盘摘要（引擎不自动下单，请结合盘面人工决策）。\n\n"
        f"{summary}\n\n"
        "======== 完整周报 ========\n\n"
        f"{md}\n"
    )


def send_email_report(days: int = 7, cfg: Optional[Config] = None,
                      force: bool = False,
                      sender: Optional[Any] = None) -> Dict[str, Any]:
    """生成周报并发送邮件。返回 {ok, skip?, error?, report_path?, week_key?}。"""
    cfg = cfg or load_config()
    days = int(days or cfg.get("weekly_email.days") or 7)
    week_key = iso_week_key()
    out: Dict[str, Any] = {"week_key": week_key, "days": days}

    if not force and not cfg.get("weekly_email.enabled", False):
        out["skip"] = "weekly_email_disabled"
        return out

    if not notify.smtp_ready(cfg):
        out["skip"] = "email_not_configured"
        return out

    today = utils.now().date()
    if not force and bool(cfg.get("weekly_email.require_friday", True)):
        if today.weekday() != 4:
            out["skip"] = "not_friday"
            return out

    if not force and not utils.is_trading_day(today):
        out["skip"] = "not_trading_day"
        return out

    if not force and bool(cfg.get("weekly_email.dedupe_per_week", True)):
        if is_sent_this_week(cfg, week_key):
            out["skip"] = "already_sent"
            return out

    wk = collect(days, cfg)
    path = write_report(days, cfg)
    out["report_path"] = path

    prefix = str(cfg.get("weekly_email.subject_prefix") or "[TEA周报]")
    res = notify.send_email(cfg, subject=email_subject(wk, cfg),
                            body=email_body(wk, cfg),
                            subject_prefix=prefix, sender=sender)
    if not res.get("ok"):
        out["ok"] = False
        out["error"] = res.get("error")
        return out

    mark_sent(cfg, week_key, meta={"since": wk.get("since"), "until": wk.get("until"),
                                    "report_path": path})
    out["ok"] = True
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
    t3 = wk.get("t3_attribution") or {}
    if t3.get("total_n"):
        tr = t3.get("total_rate") or 0.0
        lines.append(f"  ---- T+3 三日持有（方案 E）----")
        lines.append(f"    全样本 T+3>0 {tr:.0%}（{t3.get('total_up', 0)}/{t3['total_n']}）")
        if t3.get("mengya_n"):
            mr = t3.get("mengya_rate") or 0.0
            lines.append(f"    萌芽 T+3>0 {mr:.0%}（{t3.get('mengya_up', 0)}/{t3['mengya_n']}）")
        gap = t3.get("gap") or {}
        if gap.get("pending_t3"):
            lines.append(f"    待 T+3 回填 {gap['pending_t3']} 条")
    return "\n".join(lines)
