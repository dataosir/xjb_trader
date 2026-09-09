"""launchd 调度统一注册表 — 单一真相源。

所有外部定时任务的触发时刻、label、日志路径在此定义；
实际时刻从 ``scheduler.<job>.*`` 读取（见 ``config_store.DEFAULTS``）。

安装脚本通过 ``tea launchd render-plist <job>`` 生成 plist，避免模板与配置漂移。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

from tea.config.config_store import Config, load_config

_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
_WEEKDAYS_MON_FRI = (1, 2, 3, 4, 5)


def format_hm(hour: int, minute: int) -> str:
    return f"{int(hour):02d}:{int(minute):02d}"


def parse_hm(text: str) -> Tuple[int, int]:
    raw = str(text or "0:0").strip()
    if ":" not in raw:
        return 0, 0
    h, m = raw.split(":", 1)
    return int(h), int(m)


@dataclass(frozen=True)
class LaunchdJob:
    """单个 launchd 任务元数据（时刻除外，时刻走配置）。"""

    job_id: str
    label: str
    config_prefix: str
    kind: str  # calendar | interval | shell_calendar
    tea_command: Optional[str] = None
    shell_script: Optional[str] = None
    stdout_log: str = ""
    stderr_log: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind == "interval" and not self.tea_command:
            raise ValueError(f"{self.job_id}: interval 任务需要 tea_command")
        if self.kind in ("calendar", "shell_calendar") and not (self.tea_command or self.shell_script):
            raise ValueError(f"{self.job_id}: calendar 任务需要 tea_command 或 shell_script")


# 注册表：新增 launchd 任务只改此处 + config_store.scheduler + install 脚本
JOBS: Dict[str, LaunchdJob] = {
    "seed_plan": LaunchdJob(
        job_id="seed_plan",
        label="com.tea.seed-plan",
        config_prefix="scheduler.seed_plan",
        kind="calendar",
        tea_command="seed-plan",
        stdout_log="launchd-seed.stdout.log",
        stderr_log="launchd-seed.stderr.log",
        description="种子扫描 seed-plan",
    ),
    "review": LaunchdJob(
        job_id="review",
        label="com.tea.review",
        config_prefix="scheduler.review",
        kind="shell_calendar",
        shell_script="ops/review-cron.sh",
        stdout_log="launchd-review.stdout.log",
        stderr_log="launchd-review.stderr.log",
        description="盘后全量 review（T+3 回填）",
    ),
    "watch_alert": LaunchdJob(
        job_id="watch_alert",
        label="com.tea.watch-alert",
        config_prefix="scheduler.watch_alert",
        kind="interval",
        tea_command="watch-alert",
        stdout_log="launchd-watch-alert.stdout.log",
        stderr_log="launchd-watch-alert.stderr.log",
        description="盘中观察池提醒",
    ),
    "weekly_email": LaunchdJob(
        job_id="weekly_email",
        label="com.tea.weekly-email",
        config_prefix="scheduler.weekly_email",
        kind="calendar",
        tea_command="weekly-email",
        stdout_log="launchd-weekly-email.stdout.log",
        stderr_log="launchd-weekly-email.stderr.log",
        description="周五选股周报邮件",
    ),
}


def get_job(job_id: str) -> LaunchdJob:
    job = JOBS.get(job_id)
    if not job:
        known = ", ".join(sorted(JOBS))
        raise KeyError(f"未知 launchd 任务: {job_id}（可选: {known}）")
    return job


def _cfg_int(cfg: Config, key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_weekdays(cfg: Config, prefix: str, default: Sequence[int]) -> List[int]:
    raw = cfg.get(f"{prefix}.weekdays", list(default))
    if not isinstance(raw, (list, tuple)):
        return list(default)
    out: List[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out or list(default)


def calendar_spec(cfg: Config, job: LaunchdJob) -> Dict[str, Union[int, List[int]]]:
    """读取日历触发配置。"""
    p = job.config_prefix
    if job.job_id == "weekly_email":
        wd = _cfg_int(cfg, f"{p}.weekday", 5)
        return {
            "hour": _cfg_int(cfg, f"{p}.hour", 17),
            "minute": _cfg_int(cfg, f"{p}.minute", 0),
            "weekdays": [wd],
        }
    return {
        "hour": _cfg_int(cfg, f"{p}.hour", 0),
        "minute": _cfg_int(cfg, f"{p}.minute", 0),
        "weekdays": _cfg_weekdays(cfg, p, _WEEKDAYS_MON_FRI),
    }


def interval_sec(cfg: Config, job: LaunchdJob) -> int:
    return max(1, _cfg_int(cfg, f"{job.config_prefix}.interval_sec", 60))


def trigger_summary(cfg: Optional[Config] = None, job_id: Optional[str] = None) -> str:
    """人类可读的触发说明（安装脚本 / 文档 / status 用）。"""
    cfg = cfg or load_config()
    if job_id:
        return _job_trigger_summary(cfg, get_job(job_id))
    lines = []
    for jid in sorted(JOBS):
        lines.append(f"{jid}: {_job_trigger_summary(cfg, JOBS[jid])}")
    return "\n".join(lines)


def _job_trigger_summary(cfg: Config, job: LaunchdJob) -> str:
    if job.kind == "interval":
        sec = interval_sec(cfg, job)
        return f"每 {sec} 秒（{job.description}）"
    spec = calendar_spec(cfg, job)
    hm = format_hm(spec["hour"], spec["minute"])
    wds = spec["weekdays"]
    if wds == [5]:
        return f"周五 {hm}（{job.description}）"
    if list(wds) == list(_WEEKDAYS_MON_FRI):
        return f"周一至五 {hm}（{job.description}）"
    return f"周{','.join(str(w) for w in wds)} {hm}（{job.description}）"


def _xml(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _program_args(job: LaunchdJob, tea_home: str, python_exe: str) -> List[str]:
    if job.shell_script:
        return [f"{tea_home.rstrip('/')}/{job.shell_script}"]
    return [python_exe, "-m", "tea", job.tea_command or ""]


def _calendar_interval_xml(spec: Dict[str, Union[int, List[int]]]) -> str:
    wds = spec["weekdays"]
    hour = int(spec["hour"])
    minute = int(spec["minute"])
    blocks = []
    for wd in wds:
        blocks.append(
            "        <dict>\n"
            f"            <key>Weekday</key><integer>{int(wd)}</integer>\n"
            f"            <key>Hour</key><integer>{hour}</integer>\n"
            f"            <key>Minute</key><integer>{minute}</integer>\n"
            "        </dict>"
        )
    if len(blocks) == 1:
        inner = blocks[0].strip()
        return f"    <key>StartCalendarInterval</key>\n    {inner}"
    inner = "\n".join(blocks)
    return f"    <key>StartCalendarInterval</key>\n    <array>\n{inner}\n    </array>"


def render_plist(job_id: str, tea_home: str, python_exe: str = "python3",
                 cfg: Optional[Config] = None) -> str:
    """根据当前配置生成 launchd plist XML。"""
    cfg = cfg or load_config()
    job = get_job(job_id)
    home = tea_home.rstrip("/")
    args = _program_args(job, home, python_exe)
    args_xml = "\n".join(f"        <string>{_xml(a)}</string>" for a in args)

    env_extra = ""
    if not job.shell_script:
        env_extra = (
            "        <key>TEA_LAUNCHD</key>\n"
            "        <string>1</string>\n"
        )

    schedule_xml = ""
    if job.kind == "interval":
        sec = interval_sec(cfg, job)
        schedule_xml = f"    <key>StartInterval</key>\n    <integer>{sec}</integer>"
    else:
        schedule_xml = _calendar_interval_xml(calendar_spec(cfg, job))

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_xml(job.label)}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>WorkingDirectory</key>
    <string>{_xml(home)}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TEA_HOME</key>
        <string>{_xml(home)}</string>
{env_extra}        <key>PATH</key>
        <string>{_PATH}</string>
    </dict>
{schedule_xml}
    <key>StandardOutPath</key>
    <string>{_xml(home)}/logs/{job.stdout_log}</string>
    <key>StandardErrorPath</key>
    <string>{_xml(home)}/logs/{job.stderr_log}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def seed_scan_should_match(cfg: Optional[Config] = None) -> bool:
    """timing.seed_scan 应与 scheduler.seed_plan 时刻一致。"""
    cfg = cfg or load_config()
    spec = calendar_spec(cfg, JOBS["seed_plan"])
    h, m = parse_hm(cfg.get("timing.seed_scan", "14:30"))
    return h == int(spec["hour"]) and m == int(spec["minute"])
