"""数据源接口与降级链。

一个源能做什么由它自己声明：`IDataProvider` 的 fetch_* 默认全抛
`NotImplementedError`，子类只覆盖自己有能力的方法（网易没有 K 线、凤凰没有报价，
这就是常态）。`ChainedProvider` 见到 `NotImplementedError` **静默跳过**、不计失败，
继续问下一家——于是「尽可能多的源」不需要为每个数据点各配一份源列表。

单位与字段名一律以东财 schema 为准（价格元 / 成交量手 / 成交额与市值亿），
换算在各 provider 内部做完，上层拿到的东西不区分来源。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from tea.config.config_store import Config
from tea.core import utils
from ..errors import MarketError
from ..indicators import ma


def resolve_symbol(code: str = "", secid: Optional[str] = None) -> Tuple[str, str]:
    """定位一只标的，返回 (市场前缀 sh/sz, 6 位代码)。

    secid 优先：指数只有 secid 分得清（上证 1.000001 与深市平安银行 0.000001
    同码不同市），从代码本身反推市场会把上证指数认成深市股票。
    """
    if secid and "." in str(secid):
        mkt, _, raw = str(secid).partition(".")
        return ("sh" if str(mkt).strip() == "1" else "sz"), utils.norm_code(raw)
    c = utils.norm_code(code)
    return ("sh" if utils.market_of(c) == 1 else "sz"), c


def index_double_route(point_chg: Callable[[], Tuple[Optional[float], Optional[float]]],
                       klines: Callable[[], List[dict]], ma_window: int = 20) -> dict:
    """指数快照的两路取源：点位/涨跌幅走报价，MA20 走 K 线，互为兜底。

    报价整条挂掉时用最后一根 K 线的收盘当点位、与前收比出涨跌幅——两路不是一根绳，
    「当日上证价」绝大多数情况都还能显示出来。两路都拿不到点位才算真失败。
    """
    point = chg = None
    quote_err: Optional[Exception] = None
    try:
        point, chg = point_chg()
    except NotImplementedError:
        raise
    except Exception as exc:
        quote_err = exc
    rows: List[dict] = []
    try:
        rows = klines() or []
    except NotImplementedError:
        rows = []
    except Exception:
        rows = []
    ma20 = ma(rows, ma_window) if rows else None
    if point is None or chg is None:
        closes = [r.get("close") for r in rows if r.get("close") is not None]
        if point is None and closes:
            point = closes[-1]
        if chg is None and len(closes) >= 2 and closes[-2]:
            chg = round((closes[-1] / closes[-2] - 1) * 100, 2)
    if point is None:
        raise quote_err or MarketError("指数点位取数失败")
    return {
        "point": point,
        "chg_pct": chg,
        "ma20": ma20,
        "ma20_above": (ma20 is not None and point > ma20),
    }


class IDataProvider:
    """数据源接口。不支持的方法保持默认实现（抛 NotImplementedError）即可。

    注意「不支持」与「取数失败」是两件事，抛的异常也不同：
    - `NotImplementedError`：这家源根本没这个能力 → 降级链静默跳过，不计失败。
    - `MarketError`：有能力但这次没拿到 → 计一次失败，并汇总进最终错误消息。
    """

    #: 配置里 data_sources / provider_timeouts 用的源名
    name = "base"
    #: 各方法的兜底超时（秒）；配置 market.provider_timeouts.{name}.{method} 优先
    DEFAULT_TIMEOUTS: Dict[str, float] = {}
    #: 方法名 → 超时配置键（index_snapshot 的配置项写作 index，短一点）
    TIMEOUT_ALIAS = {"index_snapshot": "index"}

    def __init__(self, cfg: Config, fetcher: Any):
        self.cfg = cfg
        self.fetcher = fetcher
        self.stats = {"success": 0, "failed": 0, "skipped": 0}

    # -------------------------------------------------- 超时
    def timeout_for(self, method: str) -> float:
        """本源该方法的超时：配置 > 源内默认 > 全局 market.timeout。"""
        key = self.TIMEOUT_ALIAS.get(method, method)
        v = None
        if self.cfg is not None:
            v = utils.to_float(self.cfg.get(f"market.provider_timeouts.{self.name}.{key}"))
        if v is None:
            v = self.DEFAULT_TIMEOUTS.get(key)
        if v is None and self.cfg is not None:
            v = utils.to_float(self.cfg.get("market.timeout"), 8.0)
        return float(v if v is not None else 8.0)

    @contextmanager
    def timeout_scope(self, method: str) -> Iterator[None]:
        """在本源的请求期间临时把抓取器超时换成本源的值。

        慢源不该按东财的 8s 白等：链上后面还有四家，早失败早降级。抓取器是共享的，
        所以只在调用期间借用、结束即还原；自测里替换掉的假抓取器没有这个能力，
        此时退化成空操作。
        """
        scope = getattr(self.fetcher, "with_timeout", None)
        if scope is None:
            yield
            return
        with scope(self.timeout_for(method)):
            yield

    @contextmanager
    def retry_scope(self, key: str, default: int) -> Iterator[None]:
        """在本源的请求期间临时把抓取器重试次数换成配置值（覆盖 CDN 节点池）。

        东财的报价 / K 线都走节点池（quote 池 3 个、kline 池 4 个），全局 retries=2
        只试前两个节点，健康节点排在后面就漏掉、被迫切到备源。这里按配置覆盖整条
        池，用完即还原；自测里的假抓取器没有 with_retries 能力，退化成空操作。
        """
        scope = getattr(self.fetcher, "with_retries", None)
        if scope is None:
            yield
            return
        times = int(self.cfg.get(f"market.{key}", default) or default)
        with scope(max(1, times)):
            yield

    # -------------------------------------------------- 能力（子类按需覆盖）
    def fetch_quote(self, code: str) -> dict:
        """实时行情，返回东财 schema 的标准 dict。"""
        raise NotImplementedError(f"{self.name} 不提供实时行情")

    def fetch_klines(self, code: str, limit: Optional[int] = None,
                     secid: Optional[str] = None) -> List[dict]:
        """日 K，返回 [{date, open, close, high, low, volume, amount}]（新→旧）。"""
        raise NotImplementedError(f"{self.name} 不提供日 K 线")

    def fetch_index_snapshot(self, secid: Optional[str] = None) -> dict:
        """大盘快照，返回 {point, chg_pct, ma20, ma20_above}。"""
        raise NotImplementedError(f"{self.name} 不提供指数快照")


def _has_data(res: Any, method: str) -> bool:
    """结果是否算「拿到了数据」，按 method 精细判断（否则继续降级）。

    半成品响应（例如某家源走了异常分支只回 `{"code": "600519"}` 而缺 price）在旧的
    「非空 dict 且无 error」判据下会被误认成命中、就地停止降级。这里按各方法真正
    的关键字段验一遍，缺字段 / 零值一律当没拿到，让降级链接着问下一家。
    注：指数快照的价位字段名为 point（见 index_double_route），不是 price。
    """
    if res is None:
        return False
    if method == "quote":
        if not isinstance(res, dict) or res.get("error"):
            return False
        price = utils.to_float(res.get("price"))
        return price is not None and price > 0
    if method == "klines":
        if not isinstance(res, (list, tuple)) or not res:
            return False
        first = res[0]
        close = utils.to_float(first.get("close")) if isinstance(first, dict) else None
        return close is not None and close > 0
    if method == "index_snapshot":
        if not isinstance(res, dict) or res.get("error"):
            return False
        point = utils.to_float(res.get("point"))
        return point is not None and point > 0
    # 其它方法退回通用判据（非空且不带 error）
    if isinstance(res, dict):
        return bool(res) and not res.get("error")
    if isinstance(res, (list, tuple)):
        return bool(res)
    return True


#: 源名与方法名的中文称呼（只用于屏上提示，没登记的就原样显示）
SOURCE_LABELS = {"eastmoney": "东财", "tencent": "腾讯", "sina": "新浪",
                 "netease": "网易", "ifeng": "凤凰"}
METHOD_LABELS = {"quote": "行情", "klines": "K线", "index_snapshot": "大盘指数"}


def source_label(name: str) -> str:
    return SOURCE_LABELS.get(str(name), str(name))


def _brief(exc: Exception, limit: int = 34) -> str:
    """把异常挤成一句短词：屏上提示不需要整段 URL 与堆栈。"""
    msg = " ".join(str(exc).split())
    if not msg:
        msg = type(exc).__name__
    return msg if len(msg) <= limit else msg[:limit - 1] + "…"


class ChainedProvider(IDataProvider):
    """多源降级链：按配置顺序问，第一个拿到数据的算数。"""

    name = "chained"

    def __init__(self, providers: List[IDataProvider], cfg: Optional[Config] = None,
                 fetcher: Any = None):
        super().__init__(cfg, fetcher if fetcher is not None else getattr(
            providers[0] if providers else None, "fetcher", None))
        self.providers = list(providers)
        #: 每个方法最近一次由谁供数（观察降级是否真的发生过）
        self.last_source: Dict[str, str] = {}
        #: 源名 → 供数次数；降级链到底有没有干活，看这个最直接
        self.source_hit_count: Dict[str, int] = {}
        #: 已经报过「改用谁」的 (源, 方法)，避免同一家连挂时每条记录刷一遍
        self._fallback_notified: set = set()

    # -------------------------------------------------- 降级
    def _supports(self, provider: IDataProvider, method: str) -> bool:
        """这家有没有这个能力（没覆盖就是基类那个抛 NotImplementedError 的）。"""
        fn = getattr(type(provider), f"fetch_{method}", None)
        return fn is not None and fn is not getattr(IDataProvider, f"fetch_{method}", None)

    def _notify_fallback(self, failed: IDataProvider, method: str,
                         exc: Exception, rest: List[IDataProvider]) -> None:
        """切下家前在屏上报一声。

        同一件事抓取层报的是「网络抖动」——看不出后面还有四家备源接手，
        于是一屏「抖动」像是整个程序在碰壁。这里直接说清“谁挂了、改用谁”。
        下家只数真正有这个能力的（网易没 K 线，报「改用网易」是骗人）。
        """
        if not getattr(self.fetcher, "show_progress", False):
            return
        # 同一家源同一方法已经报过一次：回填/扫描每条记录都再刷「东财K线失败→腾讯」
        # 是无信息重复。降级链还在干活，源命中统计收尾会报总账，这里静默即可。
        key = (failed.name, method)
        if key in self._fallback_notified:
            return
        self._fallback_notified.add(key)
        nxt = next((p for p in rest if self._supports(p, method)), None)
        what = METHOD_LABELS.get(method, method)
        head = f"  ⏳ {source_label(failed.name)}{what}失败（{_brief(exc)}）"
        print(head + (f"，改用 {source_label(nxt.name)} 数据源..." if nxt else
                      "，已无备用数据源"), flush=True)

    def _try_all(self, method: str, *args, **kwargs) -> Any:
        errors: List[str] = []
        empty: List[Tuple[str, Any]] = []
        for idx, p in enumerate(self.providers):
            fn = getattr(p, f"fetch_{method}", None)
            if fn is None:
                continue
            try:
                with p.timeout_scope(method):
                    res = fn(*args, **kwargs)
            except NotImplementedError:
                # 这家没这个能力：不是故障，不进错误汇总，直接问下一家。
                p.stats["skipped"] += 1
                self.stats["skipped"] += 1
                continue
            except Exception as exc:
                p.stats["failed"] += 1
                self.stats["failed"] += 1
                errors.append(f"{p.name}: {type(exc).__name__}: {exc}")
                self._notify_fallback(p, method, exc, self.providers[idx + 1:])
                continue
            if _has_data(res, method):
                p.stats["success"] += 1
                self.stats["success"] += 1
                self._mark(method, p.name)
                return res
            empty.append((p.name, res))
        if empty:
            # 响应合法但确实没有数据（停牌 / 新股还没 K 线）。单源时代这里返回的是
            # 空列表并照常缓存，不该因为多接了几家源就把「确实没有」升级成取数失败。
            src, res = empty[0]
            self._mark(method, src)
            return res
        self.stats["failed"] += 1
        detail = " | ".join(errors) if errors else "没有可用的数据源"
        raise MarketError(f"数据源全部失败（{method}）：{detail}")

    def _mark(self, method: str, source: str) -> None:
        self.last_source[method] = source
        self.source_hit_count[source] = self.source_hit_count.get(source, 0) + 1
        stats = getattr(self.fetcher, "stats", None)
        if isinstance(stats, dict):
            stats["source_used"] = source
            by_method = stats.setdefault("source_by_method", {})
            if isinstance(by_method, dict):
                by_method[method] = source

    def source_line(self) -> str:
        """一行降级摘要（给收尾提示用）。"""
        if not self.last_source:
            return f"数据源 {'/'.join(p.name for p in self.providers)}（本次未取数）"
        return "数据源 " + "　".join(f"{k}→{v}" for k, v in sorted(self.last_source.items()))

    def provider_stats_line(self) -> str:
        """一行源命中摘要：`网络请求 68 次｜东财 45｜腾讯 18｜失败 0`。

        各源后面的数字是「供数次数」（报价/K 线/指数三类数据点），与请求次数
        不同数量级（一个数据点可能抓了几次），所以分开数。取不到抓取层统计时
        （自测里的假抓取器）只报源部分。
        """
        stats = getattr(self.fetcher, "stats", None)
        parts: List[str] = []
        if isinstance(stats, dict) and stats.get("requests"):  # 0 也 falsy，不打废话行
            parts.append(f"网络请求 {int(stats['requests'])} 次")
        # 按降级链的配置顺序列，看得出“主源扛了多少、备源接了多少”
        for p in self.providers:
            n = self.source_hit_count.get(p.name, 0)
            if n:
                parts.append(f"{source_label(p.name)} {n}")
        if isinstance(stats, dict) and stats.get("errors"):
            parts.append(f"失败 {int(stats['errors'])}")
        # 全零（没请求、没命中、没失败）时返回空串，调用方判空跳过这行装饰
        return "｜".join(parts) if parts else ""

    # -------------------------------------------------- 代理
    def fetch_quote(self, code: str) -> dict:
        return self._try_all("quote", code)

    def fetch_klines(self, code: str, limit: Optional[int] = None,
                     secid: Optional[str] = None) -> List[dict]:
        return self._try_all("klines", code, limit=limit, secid=secid)

    def fetch_index_snapshot(self, secid: Optional[str] = None) -> dict:
        return self._try_all("index_snapshot", secid=secid)
