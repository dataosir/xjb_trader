"""种子落选追溯：SEED_TRACE.md（人类可读）+ seed_trace.jsonl（机器可读）。

四步流每一步的淘汰原因都会被记录，用于复盘"为什么今天没票"。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import utils
from .config_store import Config, load_config

STEP_SECTOR = "第1步-板块综合排序"
STEP_WINDOW = "第2步-三档涨幅窗筛选"
STEP_VETO = "第3步-VETO过滤"
STEP_PREFLIGHT = "第4步-预审"


class Tracer:
    """一次扫描的落选记录器。"""

    def __init__(self, cfg: Optional[Config] = None, scan_id: Optional[str] = None):
        self.cfg = cfg or load_config()
        self.scan_id = scan_id or utils.stamp()
        self.records: List[dict] = []
        self.notes: List[str] = []

    # -------------------------------------------------- 记录
    def add(self, step: str, code: str, name: str, reason: str, detail: str = "",
            **extra: Any) -> dict:
        rec = {
            "scan_id": self.scan_id, "date": utils.today_str(),
            "ts": utils.now().strftime("%H:%M:%S"),
            "step": step, "code": code, "name": name,
            "reason": reason, "detail": detail,
        }
        rec.update(extra)
        self.records.append(rec)
        return rec

    def add_sector(self, name: str, reason: str, detail: str = "", **extra: Any) -> dict:
        return self.add(STEP_SECTOR, extra.pop("bk", ""), name, reason, detail, **extra)

    def note(self, text: str) -> None:
        self.notes.append(text)

    # -------------------------------------------------- 汇总
    def by_step(self) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = {}
        for r in self.records:
            out.setdefault(r["step"], []).append(r)
        return out

    def by_reason(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self.records:
            out[r["reason"]] = out.get(r["reason"], 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    # -------------------------------------------------- 落盘
    def flush(self) -> Dict[str, str]:
        paths = {}
        if not self.cfg.get("report.write_seed_trace", True):
            return paths
        jl = self.cfg.data_file("seed_trace_jsonl")
        for r in self.records:
            utils.append_jsonl(jl, r)
        paths["jsonl"] = jl
        paths["md"] = utils.atomic_write(
            self.cfg.report_file(self.cfg.get("paths.seed_trace_md", "SEED_TRACE.md")),
            self.render_md())
        return paths

    def render_md(self) -> str:
        lines = [
            f"# SEED_TRACE {utils.today_str()} · {self.scan_id}",
            "",
            f"落选记录 {len(self.records)} 条。",
            "",
        ]
        if self.notes:
            lines += ["## 扫描备注", ""]
            lines += [f"- {n}" for n in self.notes]
            lines.append("")
        reasons = self.by_reason()
        if reasons:
            lines += ["## 淘汰原因分布", "", "| 原因 | 数量 |", "| --- | --- |"]
            lines += [f"| {k} | {v} |" for k, v in reasons.items()]
            lines.append("")
        for step, recs in self.by_step().items():
            lines += [f"## {step}（{len(recs)} 条）", "", "| 代码 | 名称 | 原因 | 明细 |", "| --- | --- | --- | --- |"]
            for r in recs:
                lines.append(f"| {r.get('code') or '—'} | {r.get('name') or '—'} | "
                             f"{r.get('reason')} | {r.get('detail') or ''} |")
            lines.append("")
        return "\n".join(lines)


def load_traces(cfg: Optional[Config] = None, date: Optional[str] = None) -> List[dict]:
    cfg = cfg or load_config()
    recs = utils.read_jsonl(cfg.data_file("seed_trace_jsonl"))
    if date:
        recs = [r for r in recs if r.get("date") == date]
    return recs


def format_trace_summary(cfg: Optional[Config] = None, date: Optional[str] = None) -> str:
    recs = load_traces(cfg, date or utils.today_str())
    if not recs:
        return "落选追溯：今日无记录"
    counts: Dict[str, int] = {}
    for r in recs:
        counts[r.get("reason", "?")] = counts.get(r.get("reason", "?"), 0) + 1
    lines = [f"===== 落选追溯（{date or utils.today_str()}，{len(recs)} 条）====="]
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
        lines.append(f"  {v:>3}  {k}")
    return "\n".join(lines)
