"""通用工具：原子写入、JSON 读写、日期/交易日、数值与格式化、进度计时。

所有落盘文件统一走 atomic_write（写 .tmp 再 rename），避免半截文件。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import random
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

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


def latest_trading_day_str(d: Optional[_dt.date] = None) -> str:
    """最近一个交易日（今天本身若是交易日即今天），返回 YYYY-MM-DD。

    用于判断日K最后一根是否为「今日最近数据」：盘前 / 非交易日时最后一根落在
    上一交易日，技术指标据此标记 stale，共振评分相关维度弃用该指标。
    """
    cur = d or now().date()
    while not is_trading_day(cur):
        cur -= _dt.timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


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


def append_jsonl(path: str, record: dict, fsync: bool = True) -> str:
    """追加一行 JSONL（机器可读追溯）。append 语义下不做原子替换。

    默认 fsync=True：写完立刻 flush + fsync，落盘后才返回，避免进程/机器崩溃时
    丢掉留在 OS 缓存里的最后几行（accumulator / seed_trace / seed_records 都是追溯
    数据，丢行等于丢证据）。高频批量写入且可容忍丢行的极端场景可传 fsync=False。
    """
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if fsync:
            f.flush()
            os.fsync(f.fileno())
    return path


def read_jsonl(path: str, io: Any = None) -> list:
    """读 JSONL，坏行跳过但不静默：结束时汇总告警一次（io 优先，否则 stderr）。"""
    out: list = []
    bad: list = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    bad.append(lineno)
    except OSError:
        return out
    if bad:
        msg = (f"[read_jsonl] 忽略 {len(bad)} 条坏行 in {path}"
               f"（行号 {', '.join(str(n) for n in bad[:10])}"
               f"{' ...' if len(bad) > 10 else ''}）")
        if io is not None:
            tell(io, msg)
        else:
            print(msg, file=sys.stderr)
    return out


def cleanup_reports(cfg: Any = None) -> list:
    """按 paths.keep_reports 保留最新 N 份报告，删掉更旧的。

    只动 reports 目录下的 .md 文件（SEED_* / WEEKLY_* / TRADE_CHECK_* 等），不递归子
    目录、不动非 .md；SEED_TRACE.md 是持续覆写的主日志，永不删。
    keep_reports ≤ 0 视为“不清理”。返回已删文件名列表。

    落在 core（Layer 0）而非 reporting，是为了让 screening/reporting 都能直接用；
    load_config 延迟导入，避免 core → config → core 的环。
    """
    if cfg is None:
        from tea.config.config_store import load_config
        cfg = load_config()
    keep = int(to_float(cfg.get("paths.keep_reports", 200), 0) or 0)
    if keep <= 0:
        return []
    d = cfg.reports_dir()
    protected = {str(cfg.get("paths.seed_trace_md", "SEED_TRACE.md"))}
    try:
        names = os.listdir(d)
    except OSError:
        return []
    files = []
    for name in names:
        if not name.lower().endswith(".md") or name in protected:
            continue
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        try:
            files.append((os.path.getmtime(p), name, p))
        except OSError:
            continue
    if len(files) <= keep:
        return []
    files.sort(key=lambda x: (x[0], x[1]), reverse=True)
    removed = []
    for _, name, p in files[keep:]:
        try:
            os.remove(p)
            removed.append(name)
        except OSError:
            continue
    return removed


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
    """除法兜底：缺值返回 default；0 分母同样返回 default（不抛 ZeroDivisionError）。

    两种情况分开判断，语义明确：
    - a/b 任一为 None（数据缺失）→ default
    - b == 0（分母为零，含 -0.0）→ default
    需要“0 分母必须报错”的调用方请直接写 a / b，不要用本函数。
    """
    if a is None or b is None:
        return default
    if b == 0:
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


def hl(text: str, color: str = "yellow") -> str:
    """ANSI 终端高亮（粗体 + 前景色），供控制台摘要标红/标绿用。

    颜色：red / green / yellow / blue / magenta / cyan / white。
    Windows 控制台自动尝试开启 ANSI 支持；Markdown 存档走 render_md，不经本函数。
    """
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
    colors = {"red": "31", "green": "32", "yellow": "33", "blue": "34",
              "magenta": "35", "cyan": "36", "white": "37"}
    code = colors.get(color, "33")
    return f"\033[1;{code}m{text}\033[0m"


# ------------------------------------------------------------------ 统一颜色方案
# 工程规则（见 docs/CONTRIBUTING.md「颜色方案」）：业务代码里禁止散落裸颜色名，
# 一律用这些语义常量 + utils.hl / utils.sign_color。语义由含义决定，不由位置决定。

COLOR_PROFIT = "green"      # 盈利 / 上涨 / 通过
COLOR_LOSS = "red"          # 亏损 / 下跌 / 拒绝
COLOR_WARN = "yellow"       # 警告 / 注意 / 待定 / 数据缺失
COLOR_SEED = "magenta"      # 种子选中 / 可买（与盈亏色区分，单独标记）
COLOR_INFO = "cyan"         # 信息 / 强调 / 候选明细 / 复盘
COLOR_NEUTRAL = "white"     # 中性 / 零值


def sign_color(v: Optional[float]) -> str:
    """按正负号取语义色：>0 盈利绿、<0 亏损红、=0 中性白、None 警告黄。"""
    if v is None:
        return COLOR_WARN
    if v > 0:
        return COLOR_PROFIT
    if v < 0:
        return COLOR_LOSS
    return COLOR_NEUTRAL


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


_FULLWIDTH = {'０': '0', '１': '1', '２': '2', '３': '3', '４': '4', '５': '5',
              '６': '6', '７': '7', '８': '8', '９': '9', '．': '.', '－': '-'}


def normalize_digits(raw: str) -> str:
    """全角数字/小数点/负号→半角，兼容中文输入法下的误输入。"""
    return ''.join(_FULLWIDTH.get(c, c) for c in (raw or ""))


def norm_code(raw: str) -> str:
    """规范化股票代码：去前缀 sh/sz、补零到 6 位。"""
    c = (raw or "").strip().upper()
    for p in ("SH", "SZ", "BJ", "SH.", "SZ."):
        if c.startswith(p):
            c = c[len(p):]
    c = c.lstrip(".")
    c = "".join(ch for ch in c if ch.isdigit())
    return c.zfill(6) if c else ""


# ---------------------------------------------------------------- 进度/计时

def tell(io: Any, text: str) -> None:
    """可选 io 的进度提示：io 为 None（自测/库调用）时静默。"""
    if io is not None:
        io.say(text)


@contextmanager
def timed(label: str, io: Any = None, threshold: float = 0.0) -> Iterator[None]:
    """计时上下文：耗时 ≥threshold 秒才打印，命中缓存的快操作不刷屏。

    抛异常时不打印——失败的操作不该抬一个“✓”出来。
    """
    start = time.time()
    yield
    elapsed = time.time() - start
    if io is not None and elapsed >= threshold:
        io.say(f"  ✓ {label} ({elapsed:.1f}s)")
