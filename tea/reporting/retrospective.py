"""后验分析：根据扫描详细日志和后续 K 线，评估遗漏的强势股。

在周末复盘时运行，读取指定日期范围的 scan_details_{date}.json，
计算每只候选票的 T+N 日收益，并按裁决分组输出 Markdown 报告。

用法（命令行）：
    python -m tea.reporting.retrospective [start_date] [end_date]
    不传参数默认为上周一到上周五。
"""
from __future__ import annotations

import datetime
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.data.market import Market

# 后验收益窗口（交易日数量）
LOOKAHEAD_DAYS = [3, 5, 10]


# ------------------------------------------------------------------ 辅助函数

def _trading_days_from(date_str: str, count: int) -> List[str]:
    """返回从 date_str（含）往后推 count 个日历日的日期列表，用于查找后续 K 线。"""
    dates = []
    cur = utils.parse_date(date_str)
    if not cur:
        return dates
    while len(dates) < count:
        dates.append(utils.compact_date(cur))
        cur = cur + datetime.timedelta(days=1)
    return dates


def _retro_returns(code: str, start_date: str, lookahead: int,
                  mk: Market) -> Dict[int, Optional[float]]:
    """计算给定候选日之后第 N 个交易日的累计收益率（相对于候选日收盘）。"""
    try:
        klines = mk.get_klines(code)
    except Exception:
        return {d: None for d in LOOKAHEAD_DAYS}
    if not klines:
        return {d: None for d in LOOKAHEAD_DAYS}

    # 寻找 start_date 对应的日 K 线收盘价
    base_close = None
    for k in klines:
        if k.get("date") == start_date:
            base_close = float(k.get("close", 0))
            break
    if base_close is None:
        return {d: None for d in LOOKAHEAD_DAYS}

    # 建立日期到收盘价的映射
    date_map = {k["date"]: float(k.get("close", 0)) for k in klines if "date" in k}

    # 获取后续日期列表（简单日历推进，周六日难免偏差，但能满足复盘需求）
    future_dates = _trading_days_from(start_date, max(LOOKAHEAD_DAYS))
    result = {}
    for d in LOOKAHEAD_DAYS:
        target = future_dates[d - 1] if len(future_dates) >= d else None
        if target:
            close = date_map.get(target)
            if close is not None and base_close != 0:
                result[d] = round((close / base_close - 1) * 100, 2)
            else:
                result[d] = None
        else:
            result[d] = None
    return result


def _load_scan_details(start_ymd: str, end_ymd: str, cfg: Config) -> List[dict]:
    """加载指定日期范围内的扫描详细日志。"""
    records = []
    dir_path = cfg.data_dir()
    cur = utils.parse_date(start_ymd)
    end = utils.parse_date(end_ymd)
    if not cur or not end:
        return records
    while cur <= end:
        fname = f"scan_details_{utils.compact_date(cur)}.json"
        fpath = os.path.join(dir_path, fname)
        if os.path.isfile(fpath):
            raw = utils.read_json(fpath)
            if isinstance(raw, dict):
                recs = raw.get("candidates", [])
                for r in recs:
                    r["scan_date"] = raw.get("scan_date", utils.compact_date(cur))
                records.extend(recs)
        cur = cur + datetime.timedelta(days=1)
    return records


# ------------------------------------------------------------------ 报告生成

def generate_retrospective(start_ymd: str, end_ymd: str,
                           cfg: Optional[Config] = None,
                           market: Optional[Market] = None) -> str:
    """生成后验分析 Markdown 报告，按裁决分组展示收益。"""
    cfg = cfg or load_config()
    mk = market or Market(cfg)
    records = _load_scan_details(start_ymd, end_ymd, cfg)

    # 按裁决分组
    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        verdict = r.get("verdict", "未知")
        groups[verdict].append(r)

    lines: List[str] = [
        f"# 后验分析 {start_ymd} ~ {end_ymd}",
        f"生成时间：{utils.now().strftime('%Y-%m-%d %H:%M')}",
        f"扫描天数：{len({r['scan_date'] for r in records if 'scan_date' in r})}",
        f"总候选数：{len(records)}",
        "",
        "## 收益窗口说明",
        f"后验窗口：交易日收盘后第 {', '.join(str(d) for d in LOOKAHEAD_DAYS)} 日的累计收益率。",
        "正数为上涨%，负数为下跌%。",
        "",
    ]

    order = ["可买", "观察轨", "硬否决", "软否决", "近失", "数据缺", "未预审", "未知"]
    for label in order:
        items = groups.get(label, [])
        if not items:
            continue
        lines.append(f"## {label} ({len(items)} 只)")
        lines.append("")
        cols = ["代码", "名称", "日期", "板块", "裁决", "否决原因",
                *[f"D+{d} 收益" for d in LOOKAHEAD_DAYS]]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + " | ".join(["---"] * len(cols)) + "|")

        for item in items:
            code = item.get("code", "")
            name = item.get("name", "")
            date = item.get("scan_date", "")
            sector = item.get("sector_name", "")
            verdict = item.get("verdict", "")
            reason = item.get("reason", "")[:60]
            returns = _retro_returns(code, date, max(LOOKAHEAD_DAYS), mk)
            ret_strs = [f"{returns.get(d, None):+.2f}%" if returns.get(d) is not None else "—"
                        for d in LOOKAHEAD_DAYS]
            row = [code, name, date, sector, verdict, reason] + ret_strs
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # 高亮遗漏
    lines.append("## 遗漏关注")
    lines.append("")
    lines.append("以下为硬否决/软否决/观察轨中 **后续 D+5 涨幅 > 3%** 的股票，请人工复核策略是否过严。")
    lines.append("")
    flagged = []
    for label in ["硬否决", "软否决", "观察轨"]:
        for item in groups.get(label, []):
            code = item.get("code", "")
            date = item.get("scan_date", "")
            ret_dict = _retro_returns(code, date, 5, mk)
            d5 = ret_dict.get(5)
            if d5 is not None and d5 > 3.0:
                item["_d5"] = d5
                flagged.append(item)

    if flagged:
        flagged.sort(key=lambda x: x.get("_d5", 0), reverse=True)
        lines.append("| 代码 | 名称 | 日期 | 裁决 | D+5 收益 | 否决原因 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for f in flagged:
            lines.append(f"| {f['code']} | {f['name']} | {f.get('scan_date','')} | "
                         f"{f.get('verdict','')} | {f['_d5']:+.2f}% | "
                         f"{f.get('reason','')[:50]} |")
        lines.append("")
    else:
        lines.append("未发现明显遗漏。")
        lines.append("")

    lines.append("---")
    lines.append("*本报告基于历史扫描详细日志自动生成，仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def save_retrospective(start_ymd: str, end_ymd: str,
                       cfg: Optional[Config] = None,
                       market: Optional[Market] = None) -> str:
    """生成并保存后验报告到 reports/ 目录，文件名含日期。"""
    cfg = cfg or load_config()
    mk = market or Market(cfg)
    content = generate_retrospective(start_ymd, end_ymd, cfg, mk)
    today = utils.today_str()
    path = os.path.join(cfg.reports_dir(), f"retrospective_{today}.md")
    utils.ensure_dir(cfg.reports_dir())
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ------------------------------------------------------------------ 命令行入口

def _default_week() -> Tuple[str, str]:
    """返回上周一和上周五的日期（YYYY-MM-DD）。"""
    today = datetime.date.today()
    # 本周一：today - weekday()
    mon = today - datetime.timedelta(days=today.weekday())
    last_fri = mon - datetime.timedelta(days=3)          # 上周五
    last_mon = last_fri - datetime.timedelta(days=4)     # 上周一
    return last_mon.strftime("%Y-%m-%d"), last_fri.strftime("%Y-%m-%d")


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) >= 2:
        start, end = argv[0], argv[1]
    else:
        start, end = _default_week()
    cfg = load_config()
    try:
        path = save_retrospective(start, end, cfg)
    except Exception as ex:
        print(f"生成后验报告失败：{ex}", file=sys.stderr)
        return 1
    print(f"后验分析报告已生成：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
