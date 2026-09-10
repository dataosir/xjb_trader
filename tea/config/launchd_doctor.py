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


def diagnose(cfg,
             tea_home: Optional[str] = None,
             agent_dir: Optional[str] = None,
             loaded_labels: Optional[set] = None) -> Dict[str, Any]:
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
                "level": "warn",
                "job": jid,
                "message": "仍通过 shell 脚本触发（Downloads 下可能 Operation not permitted）",
            })
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

    ok = not any(i["level"] == "error" for i in issues)
    fixes: List[str] = []
    if issues:
        fixes.append(f"cd {expected_home} && ./ops/install-launchd-all.sh")
        fixes.append("或单独重装：./ops/install-launchd-{seed-plan,review,watch-alert,weekly-email}.sh")

    return {
        "ok": ok,
        "expected_home": expected_home,
        "expected_python": expected_py,
        "issues": issues,
        "jobs": jobs_out,
        "fixes": fixes,
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

    fixes = result.get("fixes") or []
    if fixes:
        lines.append("---- 建议修复 ----")
        for fix in fixes:
            lines.append(f"  {fix}")

    lines.append("")
    lines.append("总体：" + ("通过" if result.get("ok") else "需修复"))
    return "\n".join(lines)
