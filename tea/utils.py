"""通用工具：原子写入、JSON 读写、日期/交易日、数值与格式化。

所有落盘文件统一走 atomic_write（写 .tmp 再 rename），避免半截文件。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import random
import tempfile
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------- 时间/日期

CN_TZ = _dt.timezone(_dt.timedelta(hours=8))


def now() -> _dt.datetime:
    """当前时间（东八区，交易时间判定基准）。"""
    return _dt.datetime.now(CN_TZ)


def today_str(d: Optional[_dt.date] = None) -> str:
    return (d or now().date()).strftime("%Y-%m-%d")


def compact_date(d: Optional[_dt.date] = None) -> str:
    return (d or now().date()).strftime("%Y%m%d")


def parse_date(s: str) -> Optional[_dt.date]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return _dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def is_trading_day(d: _dt.date) -> bool:
    """仅按自然周判定（不含法定假日表），周六周日为非交易日。"""
    return d.weekday() < 5


def next_trading_day(d: Optional[_dt.date] = None) -> _dt.date:
    cur = (d or now().date()) + _dt.timedelta(days=1)
    while not is_trading_day(cur):
        cur += _dt.timedelta(days=1)
    return cur


def prev_trading_day(d: Optional[_dt.date] = None) -> _dt.date:
    cur = (d or now().date()) - _dt.timedelta(days=1)
    while not is_trading_day(cur):
        cur -= _dt.timedelta(days=1)
    return cur


def days_between(a: str, b: str) -> Optional[int]:
    da, db = parse_date(a), parse_date(b)
    if not da or not db:
        return None
    return (db - da).days


def stamp() -> str:
    return now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------- 文件 IO

def ensure_dir(path: str) -> str:
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def atomic_write(path: str, text: str) -> str:
    """原子写文本：同目录 .tmp 文件 + os.replace。"""
    d = os.path.dirname(os.path.abspath(path))
    ensure_dir(d)
    fd, tmp = tempfile.mkstemp(prefix=".tea_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_json(path: str, data: Any) -> str:
    return atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, default=str))


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def append_jsonl(path: str, record: dict) -> str:
    """追加一行 JSONL（机器可读追溯）。append 语义下不做原子替换。"""
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def read_jsonl(path: str) -> list:
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return out
    return out


# ---------------------------------------------------------------- 数值

def to_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None or v == "" or v == "-":
            return default
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def safe_div(a: Optional[float], b: Optional[float], default: Optional[float] = None) -> Optional[float]:
    if a is None or b in (None, 0):
        return default
    return a / b


def mean(xs: Iterable[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return sum(xs) / len(xs)


def round_lot(shares: float, lot: int = 100) -> int:
    """向下取整到整手。"""
    if shares is None or shares <= 0:
        return 0
    return int(shares // lot) * lot


def pct(v: Optional[float], nd: int = 2) -> str:
    return "—" if v is None else f"{v:.{nd}f}%"


def money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f}万"
    return f"{v:.2f}"


def num(v: Optional[float], nd: int = 2) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def jitter(base: float, spread: float) -> float:
    """带抖动的延时秒数（防封用）。"""
    lo = max(0.0, base - spread)
    return random.uniform(lo, base + spread)


def market_of(code: str) -> int:
    """secid 市场前缀：沪市/科创=1，深市/创业板=0。"""
    c = (code or "").strip()
    return 1 if c[:1] in ("6", "9") or c[:2] == "11" else 0


def board_of(code: str) -> str:
    """板块归属：main/gem(创业板)/star(科创板)/bse(北交所)。"""
    c = (code or "").strip()
    if c.startswith("688") or c.startswith("689"):
        return "star"
    if c.startswith("300") or c.startswith("301"):
        return "gem"
    if c.startswith("8") or c.startswith("4") or c.startswith("920"):
        return "bse"
    return "main"


def limit_up_pct(code: str, name: str = "") -> float:
    """涨停幅度：ST 5%，创业板/科创板 20%，其余 10%。"""
    nm = (name or "").upper()
    if "ST" in nm:
        return 5.0
    return 20.0 if board_of(code) in ("gem", "star") else 10.0


def is_st(name: str) -> bool:
    nm = (name or "").upper().replace(" ", "")
    return "ST" in nm or nm.startswith("*")


def norm_code(raw: str) -> str:
    """规范化股票代码：去前缀 sh/sz、补零到 6 位。"""
    c = (raw or "").strip().upper()
    for p in ("SH", "SZ", "BJ", "SH.", "SZ."):
        if c.startswith(p):
            c = c[len(p):]
    c = c.lstrip(".")
    c = "".join(ch for ch in c if ch.isdigit())
    return c.zfill(6) if c else ""
