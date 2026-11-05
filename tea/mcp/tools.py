from __future__ import annotations

import json
from dataclasses import dataclass

from tea.analysis import followthrough as ft_mod
from tea.analysis.sentiment import format_weather
from tea.config.config_store import load_config
from tea.core import paths
from tea.core.timing import Timing
from tea.mcp.result import TeaResult
from tea.phases.prompt import IO
from tea.runtime import runner
from tea.screening import seed_report


@dataclass(frozen=True)
class TeaToolSpec:
    name: str
    label: str
    description: str


P0_TOOL_CATALOG: list[TeaToolSpec] = [
    TeaToolSpec("tea_weather", "市场天气", "等价 `tea weather`，返回市场周期与情绪摘要"),
    TeaToolSpec("tea_seed_show", "最新 SEED", "读最新 SEED 报告或 `tea seed-show`"),
    TeaToolSpec("tea_status", "今日状态", "等价 `tea status --weather`"),
    TeaToolSpec("tea_followthrough", "跟涨看板", "等价 `tea followthrough`（只读）"),
    TeaToolSpec("tea_watch", "观察池", "读取 watch_pool.json 摘要"),
]


def _quiet_io() -> IO:
    return IO(quiet=True, interactive=False)


def _from_transcript(io: IO, *, ok: bool, exit_code: int, command: list[str]) -> TeaResult:
    body = "\n".join(io.transcript).strip()
    return TeaResult(
        ok=ok,
        stdout=body,
        stderr="",
        exit_code=exit_code,
        command=command,
    ).normalized()


def _latest_seed_markdown() -> TeaResult:
    home = paths.data_dir()
    reports = home / "reports"
    if not reports.is_dir():
        return TeaResult(
            ok=False,
            stdout="",
            stderr=f"reports 目录不存在: {reports}",
            exit_code=1,
            command=[],
        ).normalized()
    files = sorted(reports.glob("SEED_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return TeaResult(ok=False, stdout="", stderr="暂无 SEED 报告", exit_code=1, command=[]).normalized()
    latest = files[0]
    try:
        body = latest.read_text(encoding="utf-8")
    except OSError as exc:
        return TeaResult(ok=False, stdout="", stderr=str(exc), exit_code=1, command=[]).normalized()
    header = f"文件: {latest.name}\n\n"
    return TeaResult(
        ok=True,
        stdout=header + body,
        stderr="",
        exit_code=0,
        command=["read", str(latest)],
    ).normalized()


def _watch_pool_summary() -> TeaResult:
    path = paths.data_dir() / "data" / "watch_pool.json"
    if not path.is_file():
        return TeaResult(ok=False, stdout="", stderr="watch_pool.json 不存在", exit_code=1, command=[]).normalized()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return TeaResult(ok=False, stdout="", stderr=str(exc), exit_code=1, command=[]).normalized()
    if isinstance(data, list):
        codes = [str(x.get("code", x)) if isinstance(x, dict) else str(x) for x in data]
        text = f"观察池 {len(codes)} 只: {', '.join(codes[:30])}"
        if len(codes) > 30:
            text += " …"
        return TeaResult(ok=True, stdout=text, stderr="", exit_code=0, command=["read", str(path)]).normalized()
    return TeaResult(ok=True, stdout=str(data)[:4000], stderr="", exit_code=0, command=["read", str(path)]).normalized()


def execute_tea_tool_by_name(tool_name: str) -> TeaResult:
    name = tool_name.strip()
    if name == "tea_weather":
        io = _quiet_io()
        cfg = load_config()
        sent = runner.weather(cfg, refresh=False, io=io)
        io.say(format_weather(sent))
        io.say(f"  {Timing(cfg).describe()}")
        exit_code = 0 if sent.get("allow_new") else 1
        return _from_transcript(io, ok=(exit_code == 0), exit_code=exit_code, command=["weather"])
    if name == "tea_seed_show":
        io = _quiet_io()
        cfg = load_config()
        code = seed_report.show_latest(cfg=cfg, io=io, prefer_today=True)
        if code == 0 and io.transcript:
            return _from_transcript(io, ok=True, exit_code=0, command=["seed-show"])
        fallback = _latest_seed_markdown()
        if fallback.ok:
            return fallback
        return _from_transcript(io, ok=False, exit_code=code or 1, command=["seed-show"])
    if name == "tea_status":
        io = _quiet_io()
        cfg = load_config()
        runner.daily_status(cfg=cfg, io=io, with_weather=True)
        return _from_transcript(io, ok=True, exit_code=0, command=["status", "--weather"])
    if name == "tea_followthrough":
        io = _quiet_io()
        cfg = load_config()
        io.say(ft_mod.format_followthrough(cfg))
        io.say(ft_mod.format_sample_gap(cfg))
        io.say(ft_mod.format_shadow_status(cfg))
        io.say(ft_mod.format_stage_b_status(cfg))
        io.say(ft_mod.format_lowbuy_status(cfg))
        return _from_transcript(io, ok=True, exit_code=0, command=["followthrough"])
    if name == "tea_watch":
        return _watch_pool_summary()
    return TeaResult(ok=False, stdout="", stderr=f"未知工具: {name}", exit_code=1, command=[]).normalized()


def tea_tool_result_payload(tool_name: str) -> dict:
    result = execute_tea_tool_by_name(tool_name)
    return {
        "tool": tool_name,
        "ok": result.ok,
        "exitCode": result.exit_code,
        "text": result.text,
        "command": result.command,
    }


def tea_tools_catalog_payload() -> dict:
    items = [
        {"name": spec.name, "label": spec.label, "description": spec.description}
        for spec in P0_TOOL_CATALOG
    ]
    return {"tools": items}
