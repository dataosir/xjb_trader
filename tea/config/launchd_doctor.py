"""launchd 健康检查：对比已安装 plist 与当前运行环境。

用于仓库迁移、Python 升级或 launchd 静默失败后快速定位路径漂移。
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from tea.config import config_store
from tea.config.schedules import JOBS, get_job, seed_scan_should_match


def _norm_path(raw: str) -> str:
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return str(Path(raw).expanduser())


def _launchctl_labels() -> set:
    try:
        proc = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    labels: set = set()
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            label = parts[-1]
            if label.startswith("com.tea."):
                labels.add(label)
    return labels


def _load_plist(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as fh:
            return plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def _expected_python() -> str:
    return _norm_path(sys.executable)


def clear_launchd_stderr_logs(cfg,
                              reset_sync_state: bool = True) -> Dict[str, Any]:
    """清空 launchd stderr 日志并重置增量同步状态（重装 plist 后清除旧误报）。"""
    cfg = cfg or load_config()
    log_dir = Path(cfg.logs_dir())
    cleared: List[str] = []
    for jid in JOBS:
        job = get_job(jid)
        path = log_dir / job.stderr_log
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 0:
                path.write_text("", encoding="utf-8")
                cleared.append(str(path))
        except OSError:
            continue

    if reset_sync_state:
        state_path = Path(cfg.data_file("launchd_stderr_state_file"))
        try:
            if state_path.is_file():
                state_path.write_text("{}", encoding="utf-8")
        except OSError:
            pass

    return {"cleared": cleared, "count": len(cleared)}


def _stderr_looks_stale(plist: dict, stderr_text: str) -> bool:
    """旧 plist（bash/错误 Python）遗留的 stderr 与当前直调 Python 不一致。"""
    if not stderr_text.strip():
        return False
    args = plist.get("ProgramArguments") or []
    uses_shell = any(str(a).endswith(".sh") for a in args)
    if uses_shell:
        return False
    stale_markers = (
        "Operation not permitted",
        "No module named tea",
        "seed-plan-cron.sh",
        "review-cron.sh",
        "watch-alert-cron.sh",
        "weekly-email-cron.sh",
    )
    return any(m in stderr_text for m in stale_markers)


def diagnose(cfg,
             tea_home: Optional[str] = None,
             agent_dir: Optional[str] = None,
             loaded_labels: Optional[set] = None,
             fix: bool = False) -> Dict[str, Any]:
    """检查 launchd plist 是否与当前 TEA_HOME / Python 一致。"""
    expected_home = _norm_path(tea_home or config_store.home_dir())
    expected_py = _expected_python()
    agent = Path(agent_dir or (Path.home() / "Library" / "LaunchAgents"))
    loaded = loaded_labels if loaded_labels is not None else _launchctl_labels()

    issues: List[Dict[str, str]] = []
    jobs_out: List[Dict[str, Any]] = []

    for jid in sorted(JOBS):
        job = get_job(jid)
        label = job.label
        plist_path = agent / f"{label}.plist"
        entry: Dict[str, Any] = {
            "job_id": jid,
            "label": label,
            "plist_path": str(plist_path),
            "plist_exists": plist_path.is_file(),
            "loaded": label in loaded,
        }

        if not plist_path.is_file():
            issues.append({
                "level": "error",
                "job": jid,
                "message": f"plist 不存在：{plist_path}",
            })
            jobs_out.append(entry)
            continue

        plist = _load_plist(plist_path)
        if not plist:
            issues.append({
                "level": "error",
                "job": jid,
                "message": "plist 无法解析",
            })
            jobs_out.append(entry)
            continue

        wd = _norm_path(str(plist.get("WorkingDirectory") or ""))
        env = plist.get("EnvironmentVariables") or {}
        plist_home = _norm_path(str(env.get("TEA_HOME") or wd))
        args = plist.get("ProgramArguments") or []
        plist_py = _norm_path(str(args[0])) if args else ""
        uses_shell = any(str(a).endswith(".sh") for a in args)

        entry.update({
            "tea_home": plist_home,
            "working_directory": wd,
            "python": plist_py,
            "tea_home_ok": plist_home == expected_home,
            "python_ok": plist_py == expected_py,
            "uses_shell_script": uses_shell,
        })

        if plist_home != expected_home:
            issues.append({
                "level": "error",
                "job": jid,
                "message": f"TEA_HOME 不一致：plist={plist_home} 当前={expected_home}",
            })
        if wd and wd != expected_home:
            issues.append({
                "level": "warn",
                "job": jid,
                "message": f"WorkingDirectory 不一致：plist={wd} 当前={expected_home}",
            })
        if plist_py and plist_py != expected_py:
            issues.append({
                "level": "warn",
                "job": jid,
                "message": f"Python 不一致：plist={plist_py} 当前={expected_py}",
            })
        if uses_shell:
            issues.append({
                "level": "error",
                "job": jid,
                "message": (
                    "仍通过 shell 脚本触发（Downloads 下会 Operation not permitted）；"
                    "请重装：./ops/install-launchd-all.sh"
                ),
            })
        stderr_path = Path(cfg.logs_dir()) / job.stderr_log
        stderr_text = ""
        if stderr_path.is_file():
            try:
                stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                stderr_text = ""
        if stderr_text.strip() and _stderr_looks_stale(plist, stderr_text):
            issues.append({
                "level": "warn",
                "job": jid,
                "message": (
                    f"stderr 含旧版误报（{stderr_path.name}）；"
                    "plist 已直调 Python 时可清空：tea launchd doctor --fix"
                ),
            })
            entry["stale_stderr"] = True
        if label not in loaded:
            issues.append({
                "level": "error",
                "job": jid,
                "message": "launchd 未加载",
            })

        jobs_out.append(entry)

    if not seed_scan_should_match(cfg):
        issues.append({
            "level": "warn",
            "job": "seed_plan",
            "message": "timing.seed_scan 与 scheduler.seed_plan 时刻不一致",
        })

    if "Downloads" in expected_home:
        issues.append({
            "level": "warn",
            "job": "_env",
            "message": "仓库在 Downloads 下，macOS 可能限制 launchd；建议迁至 ~/Projects 或固定 TEA_HOME",
        })

    cleared_stderr: Dict[str, Any] = {}
    if fix:
        cleared_stderr = clear_launchd_stderr_logs(cfg)
        issues = [
            i for i in issues
            if "stderr 含旧版误报" not in i.get("message", "")
        ]

    stderr_sync: Dict[str, Any] = {}
    try:
        from tea.reporting.ops_summary import sync_launchd_stderr
        stderr_sync = sync_launchd_stderr(cfg)
        if stderr_sync.get("errors"):
            issues.append({
                "level": "warn",
                "job": "_stderr",
                "message": (
                    f"launchd stderr 新增 {stderr_sync.get('errors')} 条已写入 error.log"
                ),
            })
    except Exception as ex:
        issues.append({
            "level": "warn",
            "job": "_stderr",
            "message": f"stderr 同步失败：{ex}",
        })

    ok = not any(i["level"] == "error" for i in issues)
    fixes: List[str] = []
    if issues:
        fixes.append(f"cd {expected_home} && ./ops/install-launchd-all.sh")
        fixes.append("或单独重装：./ops/install-launchd-{seed-plan,review,watch-alert,weekly-email}.sh")
        if any("stderr 含旧版误报" in i.get("message", "") for i in issues):
            fixes.append("清空旧 stderr：tea launchd doctor --fix")

    return {
        "ok": ok,
        "expected_home": expected_home,
        "expected_python": expected_py,
        "issues": issues,
        "jobs": jobs_out,
        "fixes": fixes,
        "stderr_sync": stderr_sync,
        "cleared_stderr": cleared_stderr,
    }


def format_report(result: Dict[str, Any]) -> str:
    """人类可读诊断报告。"""
    lines = [
        "===== launchd 健康检查 =====",
        f"当前 TEA_HOME：{result.get('expected_home')}",
        f"当前 Python：{result.get('expected_python')}",
        "",
    ]
    for job in result.get("jobs") or []:
        status = "✓" if job.get("plist_exists") and job.get("loaded") else "✗"
        loaded = "已加载" if job.get("loaded") else "未加载"
        lines.append(f"{status} {job.get('label')}（{loaded}）")
        if job.get("plist_exists"):
            home_ok = "✓" if job.get("tea_home_ok") else "✗"
            py_ok = "✓" if job.get("python_ok") else "✗"
            lines.append(f"    TEA_HOME {home_ok}  {job.get('tea_home') or '—'}")
            lines.append(f"    Python   {py_ok}  {job.get('python') or '—'}")
            if job.get("uses_shell_script"):
                lines.append("    ⚠ 仍使用 shell 脚本触发")
        else:
            lines.append(f"    plist 缺失：{job.get('plist_path')}")
        lines.append("")

    issues = result.get("issues") or []
    if issues:
        lines.append("---- 问题 ----")
        for item in issues:
            tag = "错误" if item.get("level") == "error" else "警告"
            lines.append(f"  [{tag}] {item.get('job')}: {item.get('message')}")
        lines.append("")

    cleared = result.get("cleared_stderr") or {}
    if cleared.get("count"):
        lines.append("---- 已清理 ----")
        lines.append(f"  已清空 {cleared.get('count')} 个 launchd stderr 日志（旧误报）")
        lines.append("")

    fixes = result.get("fixes") or []
    if fixes:
        lines.append("---- 建议修复 ----")
        for fix in fixes:
            lines.append(f"  {fix}")

    lines.append("")
    lines.append("总体：" + ("通过" if result.get("ok") else "需修复"))
    return "\n".join(lines)
