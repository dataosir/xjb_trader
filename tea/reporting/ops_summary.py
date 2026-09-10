"""日终运维摘要：launchd stderr → error.log + 任务心跳邮件（F12 扩展）。

- ``sync_launchd_stderr``：增量读取 ``logs/launchd-*.stderr.log``，去重后写入 ``error.log``。
- ``send_daily_summary``：盘后汇总 seed / review / watch-alert / error 状态发运维邮件。
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from tea.config.config_store import Config, load_config
from tea.config.schedules import JOBS, get_job
from tea.core import logger as logger_mod, notify, utils

_STATE_FILE = "launchd_stderr_state_file"
_SUMMARY_STATE_FILE = "ops_summary_state_file"


def _stderr_state_path(cfg: Config) -> str:
    return cfg.data_file(_STATE_FILE)


def _summary_state_path(cfg: Config) -> str:
    return cfg.data_file(_SUMMARY_STATE_FILE)


def _load_stderr_state(cfg: Config) -> dict:
    return utils.read_json(_stderr_state_path(cfg), default={}) or {}


def _save_stderr_state(state: dict, cfg: Config) -> None:
    utils.write_json(_stderr_state_path(cfg), state)


def _load_summary_state(cfg: Config) -> dict:
    return utils.read_json(_summary_state_path(cfg), default={}) or {}


def _save_summary_state(state: dict, cfg: Config) -> None:
    utils.write_json(_summary_state_path(cfg), state)


def _file_inode(path: str) -> Optional[int]:
    try:
        return os.stat(path).st_ino
    except OSError:
        return None


def _stderr_source_name(stderr_log: str) -> str:
    base = os.path.basename(stderr_log)
    if base.endswith(".stderr.log"):
        return base[:-len(".stderr.log")]
    return base


def sync_launchd_stderr(cfg: Optional[Config] = None,
                        jobs: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """增量同步 launchd stderr 到 ``error.log``。返回 {files, lines, errors, messages}。"""
    cfg = cfg or load_config()
    log_dir = cfg.logs_dir()
    state = _load_stderr_state(cfg)
    files_state: Dict[str, Any] = state.setdefault("files", {})

    out: Dict[str, Any] = {"files": 0, "lines": 0, "errors": 0, "messages": []}
    job_ids = list(jobs or JOBS.keys())

    for jid in job_ids:
        job = get_job(jid)
        path = os.path.join(log_dir, job.stderr_log)
        if not os.path.isfile(path):
            continue

        try:
            size = os.path.getsize(path)
        except OSError:
            continue

        inode = _file_inode(path)
        meta = files_state.get(path) or {}
        offset = int(meta.get("offset") or 0)
        if inode is not None and meta.get("inode") not in (None, inode):
            offset = 0
        if offset > size:
            offset = 0

        if size <= offset:
            files_state[path] = {"offset": size, "inode": inode}
            continue

        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read(size - offset)
        except OSError:
            continue

        files_state[path] = {"offset": size, "inode": inode}
        if not chunk:
            continue

        text = chunk.decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue

        out["files"] += 1
        out["lines"] += len(lines)
        source = _stderr_source_name(job.stderr_log)
        counts = Counter(lines)
        for msg, n in counts.items():
            note = f"{msg} (×{n})" if n > 1 else msg
            logger_mod.append_error_log(note, source=f"launchd:{source}", cfg=cfg)
            out["errors"] += 1
            out["messages"].append({"source": source, "message": msg, "count": n})

    _save_stderr_state(state, cfg)
    return out


def _iter_tea_log_paths(log_dir: str, day: str) -> List[str]:
    paths: List[str] = []
    base = os.path.join(log_dir, "tea.log")
    if os.path.isfile(base):
        paths.append(base)
    rotated = os.path.join(log_dir, f"tea.log.{day}")
    if os.path.isfile(rotated):
        paths.append(rotated)
    return paths


def _count_log_substrings(log_dir: str, day: str, needles: Sequence[str]) -> int:
    total = 0
    for path in _iter_tea_log_paths(log_dir, day):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.startswith(day):
                        continue
                    if any(n in line for n in needles):
                        total += 1
        except OSError:
            pass
    return total


def _read_error_log_today(cfg: Config, day: str, limit: int = 20) -> List[str]:
    path = logger_mod.error_log_path(cfg)
    if not os.path.isfile(path):
        return []
    lines: List[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(day):
                    lines.append(line.rstrip())
    except OSError:
        return []
    if len(lines) > limit:
        return lines[-limit:]
    return lines


def _daily_log_exists(category: str, day: str, cfg: Config) -> bool:
    return os.path.isfile(logger_mod.daily_log_path(category, cfg, day=day))


def _task_status(name: str, ok: bool, detail: str, level: str = "ok") -> dict:
    return {"name": name, "ok": ok, "detail": detail, "level": level}


def collect_daily_status(day: Optional[str] = None, cfg: Optional[Config] = None,
                         review_out: Optional[dict] = None,
                         stderr_res: Optional[dict] = None) -> Dict[str, Any]:
    """汇总当日各定时任务与错误日志状态。"""
    cfg = cfg or load_config()
    day = day or utils.today_str()
    log_dir = cfg.logs_dir()
    trading = utils.is_trading_day(utils.parse_date(day) or utils.now().date())
    alert_min = max(1, int(cfg.get("ops_summary.alert_heartbeat_min", 30)))

    seed_daily = _daily_log_exists("seed", day, cfg)
    seed_log_n = _count_log_substrings(log_dir, day, ("种子扫描开始",))
    if seed_daily or seed_log_n:
        seed = _task_status("seed-plan", True,
                            f"日录={'有' if seed_daily else '无'} tea.log={seed_log_n} 次")
    elif trading:
        seed = _task_status("seed-plan", False, "今日未见种子扫描记录", "warn")
    else:
        seed = _task_status("seed-plan", True, "非交易日（跳过）", "skip")

    review_skip = (review_out or {}).get("skip")
    review_daily = _daily_log_exists("review", day, cfg)
    if review_out and not review_skip:
        ft = review_out.get("followthrough") or {}
        review = _task_status(
            "review", True,
            f"updated={ft.get('updated')} pending={ft.get('pending')}")
    elif review_skip == "already_done" or review_daily:
        review = _task_status("review", True, f"已完成（{review_skip or '日录有'}）")
    elif review_skip:
        review = _task_status("review", True, f"跳过 {review_skip}", "skip")
    elif trading:
        review = _task_status("review", False, "今日未见盘后复核", "warn")
    else:
        review = _task_status("review", True, "非交易日（跳过）", "skip")

    alert_n = _count_log_substrings(log_dir, day, ("tea.alert",))
    alert_daily = _daily_log_exists("watch_alert", day, cfg)
    if trading and alert_n < alert_min:
        watch = _task_status(
            "watch-alert", False,
            f"盘中心跳不足（tea.alert {alert_n} 条，阈值 ≥{alert_min}）", "warn")
    else:
        watch = _task_status(
            "watch-alert", True,
            f"tea.alert {alert_n} 条" + (f"，日录={'有' if alert_daily else '无'}" if trading else "（非交易日）"))

    stderr_res = stderr_res or {}
    stderr_n = int(stderr_res.get("errors") or 0)
    if stderr_n:
        stderr_st = _task_status(
            "launchd stderr", False,
            f"新增 {stderr_n} 条已写入 error.log", "warn")
    else:
        stderr_st = _task_status("launchd stderr", True, "无新增 stderr 错误")

    err_lines = _read_error_log_today(cfg, day)
    if err_lines:
        err_st = _task_status("error.log", False, f"今日 {len(err_lines)} 条 ERROR", "warn")
    else:
        err_st = _task_status("error.log", True, "今日无 ERROR")

    tasks = [seed, review, watch, stderr_st, err_st]
    issues = [t for t in tasks if t.get("level") == "warn"]
    return {
        "date": day,
        "trading_day": trading,
        "tasks": tasks,
        "issues": issues,
        "error_lines": err_lines,
        "stderr_sync": stderr_res,
        "alert_count": alert_n,
    }


def format_summary_text(status: dict, cfg: Optional[Config] = None) -> str:
    """运维摘要纯文本（邮件正文）。"""
    cfg = cfg or load_config()
    day = status.get("date") or utils.today_str()
    issues = status.get("issues") or []
    headline = "正常" if not issues else f"需关注（{len(issues)} 项）"

    lines = [
        f"TEA 日终运维摘要 · {day}",
        f"交易日：{'是' if status.get('trading_day') else '否'}　总体：{headline}",
        "",
        "—— 定时任务 ——",
    ]
    for t in status.get("tasks") or []:
        mark = "✓" if t.get("ok") else "!"
        lines.append(f"  {mark} {t.get('name')}: {t.get('detail')}")

    err_lines = status.get("error_lines") or []
    if err_lines:
        lines.extend(["", "—— 今日 error.log（最近）——"])
        for ln in err_lines:
            lines.append(f"  {ln}")

    stderr_msgs = (status.get("stderr_sync") or {}).get("messages") or []
    if stderr_msgs:
        lines.extend(["", "—— 本次 stderr 同步 ——"])
        for m in stderr_msgs[:10]:
            cnt = m.get("count") or 1
            suffix = f" (×{cnt})" if cnt > 1 else ""
            lines.append(f"  [{m.get('source')}] {m.get('message')}{suffix}")

    lines.extend([
        "",
        "排查：grep \"$(date +%Y-%m-%d)\" logs/error.log",
        "      ls logs/daily/seed/ logs/daily/review/",
        "      tea launchd doctor",
    ])
    return "\n".join(lines)


def _email_subject(status: dict, cfg: Config) -> str:
    day = status.get("date") or utils.today_str()
    issues = status.get("issues") or []
    tag = "正常" if not issues else f"{len(issues)}项异常"
    return f"日终摘要 {day} · {tag}"


def is_sent_today(cfg: Optional[Config] = None, day: Optional[str] = None) -> bool:
    cfg = cfg or load_config()
    day = day or utils.today_str()
    return (_load_summary_state(cfg).get("last_date") or "") == day


def mark_sent(cfg: Optional[Config] = None, day: Optional[str] = None,
              meta: Optional[dict] = None) -> None:
    cfg = cfg or load_config()
    day = day or utils.today_str()
    state = _load_summary_state(cfg)
    state["last_date"] = day
    state["last_sent"] = utils.now().isoformat(timespec="seconds")
    if meta:
        state["last_meta"] = meta
    _save_summary_state(state, cfg)


def send_daily_summary(cfg: Optional[Config] = None, force: bool = False,
                       review_out: Optional[dict] = None,
                       stderr_res: Optional[dict] = None,
                       sender: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """发送日终运维摘要邮件。"""
    cfg = cfg or load_config()
    day = utils.today_str()
    out: Dict[str, Any] = {"date": day}

    if not force and not cfg.get("ops_summary.enabled", True):
        out["skip"] = "ops_summary_disabled"
        return out

    if not notify.smtp_ready(cfg):
        out["skip"] = "email_not_configured"
        return out

    trading = utils.is_trading_day(utils.now().date())
    if not force and not trading:
        out["skip"] = "not_trading_day"
        return out

    if not force and bool(cfg.get("ops_summary.dedupe_per_day", True)):
        if is_sent_today(cfg, day):
            out["skip"] = "already_sent"
            return out

    if stderr_res is None:
        stderr_res = sync_launchd_stderr(cfg)

    status = collect_daily_status(day, cfg, review_out=review_out, stderr_res=stderr_res)
    out["status"] = status

    if not force and bool(cfg.get("ops_summary.only_on_issues", False)):
        if not status.get("issues"):
            out["skip"] = "no_issues"
            return out

    body = format_summary_text(status, cfg)
    prefix = str(cfg.get("ops_summary.subject_prefix") or "[TEA运维]")
    res = notify.send_email(cfg, subject=_email_subject(status, cfg),
                            body=body, subject_prefix=prefix, sender=sender)
    if not res.get("ok"):
        out["ok"] = False
        out["error"] = res.get("error")
        logger_mod.append_daily_log("ops_summary", f"send failed {res.get('error')}", cfg)
        return out

    mark_sent(cfg, day, meta={"issues": len(status.get("issues") or [])})
    logger_mod.append_daily_log(
        "ops_summary", f"sent issues={len(status.get('issues') or [])}", cfg)
    out["ok"] = True
    return out


def maybe_send_after_review(cfg: Optional[Config] = None, review_out: Optional[dict] = None,
                            stderr_res: Optional[dict] = None,
                            force: bool = False) -> Dict[str, Any]:
    """scheduled_review 收尾：可选发运维摘要（默认 review 成功后发送）。"""
    cfg = cfg or load_config()
    if not force and not cfg.get("ops_summary.send_after_review", True):
        return {"skip": "send_after_review_disabled"}
    skip = (review_out or {}).get("skip")
    if not force and skip and skip not in ("already_done",):
        return {"skip": f"review_{skip}"}
    return send_daily_summary(cfg, force=force, review_out=review_out, stderr_res=stderr_res)
