"""采集前健康探测：先花两秒确认谁通，再决定这一轮问谁。

一次全挂的教训是「24 次请求全喂给了不可达的源」：本机 DNS 把网易/凤凰解析到保留
网段、CA 证书库是空的，主流程却仍按配置顺序一家家撞重试，屏上只有满屏「网络抖动」。
这里在采集开始前并发探一遍：

- 每家源发一条最轻的真实请求（走它自己的 fetch_quote / fetch_index_snapshot），
- 东财两套 CDN 节点池逐个探测，
- 失败按 `classify_error` 归类，DNS 劫持 / 证书 / 超时分得清。

探测只影响本次会话：不可达的源写进降级链熔断、坏节点写进抓取器死节点集合，
后续取数直接跳过，不等待超时。不改配置、不改评分口径。
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional

from tea.config.config_store import Config
from tea.core import logger as logger_mod, utils
from tea.data.errors import MarketError
from tea.data.fetcher import classify_error
from tea.data.providers.base import IDataProvider, source_label

_LOG = logger_mod.get_logger("data")

#: 探测 CDN 节点用指数 secid：报价接口按 「市场.代码」 取值，换成个股代码反而不通用。
_PROBE_SECID = "1.000001"
#: 探活源用的个股：四家报价源都覆盖，且不占指数的双路逻辑。
_PROBE_CODE = "600519"
_METHODS = ("quote", "klines", "index_snapshot")


def _supports(provider: Any, method: str) -> bool:
    """这家源有没有这个方法（没覆盖就是基类那个抛 NotImplementedError 的）。"""
    fn = getattr(type(provider), f"fetch_{method}", None)
    return fn is not None and fn is not getattr(IDataProvider, f"fetch_{method}", None)


def _probe_one(provider: Any, fetcher: Any, timeout: float) -> dict:
    """探活一家源：一次报价请求即可。

    不用指数快照：它内部是「报价 + K 线」双路取数，一次探测会变成 2~3 次请求，
    连不上时还要各等一轮超时，探活的成本就超过了它省下的。
    """
    methods = [m for m in ("quote", "index_snapshot") if _supports(provider, m)]
    if not methods:
        return {"ok": False, "kind": "unsupported", "hint": "无探测接口", "ms": 0}
    t0 = time.time()
    method, last = methods[0], None
    try:
        with fetcher.with_retries(1), fetcher.with_timeout(timeout):
            res = provider.fetch_quote(_PROBE_CODE)
        if isinstance(res, dict) and not res.get("error"):
            return {"ok": True, "kind": "ok", "hint": "", "method": method,
                    "ms": int((time.time() - t0) * 1000)}
        last = MarketError("报价返回空数据")
    except Exception as exc:
        last = exc
    kind, hint = classify_error(last) if last is not None else ("http", "无响应")
    return {"ok": False, "kind": kind, "hint": hint,
            "ms": int((time.time() - t0) * 1000)}


def _probe_host(cfg: Config, fetcher: Any, host: str, timeout: float) -> dict:
    """探活一个 CDN 节点：换掉报价 URL 的 netloc 打一次。"""
    url = fetcher._swap_host(cfg.get("market.quote_url"), host)
    out = fetcher.probe(url, {"secid": _PROBE_SECID, "fields": "f43"}, timeout=timeout)
    return {"host": host, "ok": out["ok"], "kind": out["kind"],
            "hint": out["hint"], "ms": out["ms"]}


class _Quiet:
    """探测期间关掉抓取器的「网络抖动」播报与失败 warning（只改本实例属性）。

    探测注定会撞上不可达的源，若沿用采集阶段的报信方式，用户会先看到一串
    「网络抖动…即将切换」再看到「本次跳过」，前后矛盾。探测结论统一由 _report 说。
    """

    def __init__(self, fetcher: Any):
        self.fetcher = fetcher
        self._saved = None
        self._log = None

    def __enter__(self) -> "_Quiet":
        f = self.fetcher
        self._saved = (getattr(f, "show_progress", False),
                       getattr(f, "retry_notice_gap", None))
        if hasattr(f, "show_progress"):
            f.show_progress = False
        if hasattr(f, "retry_notice_gap"):
            f.retry_notice_gap = float("inf")  # 单次探测也走 _request 的兜底提示，一并关掉
        self._log = _LOG.propagate, _LOG.level
        _LOG.propagate = True
        _LOG.setLevel(logging.CRITICAL + 1)  # fetcher 用同名 logger 写失败 warning
        return self
    def __exit__(self, *exc_info) -> None:
        self.restore()

    def restore(self) -> None:
        if self._saved is None:
            return
        show, gap = self._saved
        if hasattr(self.fetcher, "show_progress"):
            self.fetcher.show_progress = show
        if gap is not None and hasattr(self.fetcher, "retry_notice_gap"):
            self.fetcher.retry_notice_gap = gap
        self._saved = None
        if self._log is not None:
            _LOG.propagate, _LOG.level = self._log
            self._log = None


def _verdict(sources: dict) -> str:
    judged = {k: v for k, v in sources.items() if v.get("kind") != "unsupported"}
    total = len(judged) or 1
    failed = [k for k, v in judged.items() if not v.get("ok")]
    hijacked = [k for k, v in judged.items() if v.get("kind") == "dns_hijack"]
    if not failed:
        return "ok"
    if len(hijacked) >= 3:
        return "dns_blocked"
    if len(failed) * 2 >= total:
        return "down"
    return "partial"


def probe_providers(cfg: Config, fetcher: Any, io: Any = None,
                    chain: Any = None) -> dict:
    """并发探活各家源与 CDN 节点，返回报告（不抛异常）。

    预算由 `market.probe_timeout_sec` 控制：单个探测按这个超时掐断，
    整体用 `as_completed` 收结果，超预算的源记 timeout 而非卡住采集启动。
    """
    timeout = float(cfg.get("market.probe_timeout_sec", 2.5) or 2.5)
    providers: List[Any] = list(getattr(chain, "providers", None) or [])
    pools = {k: list(cfg.get(f"market.{k}") or [])
             for k in ("cdn_hosts_quote", "cdn_hosts_kline")}
    t0 = time.time()
    report: dict = {"sources": {}, "hosts": {}, "skipped": [],
                    "verdict": "ok", "elapsed": 0.0}
    if not providers:
        return report

    # 探测会撞上注定失败的源，不该在屏上刷「网络抖动…即将切换」，也不该按失败写
    # warning 日志——那是采集阶段的报信方式，这里由 _report 统一汇报。
    quiet = _Quiet(fetcher)
    try:
        with quiet, ThreadPoolExecutor(max_workers=6) as ex:
            s_futs = {ex.submit(_probe_one, p, fetcher, timeout): p.name for p in providers}
            h_futs = {}
            for pool, hosts in pools.items():
                for h in hosts:
                    h_futs[ex.submit(_probe_host, cfg, fetcher, h, timeout)] = (pool, h)
            for fut, name in s_futs.items():
                try:
                    report["sources"][name] = fut.result()
                except Exception as exc:  # 探测自身出错不该拖垮流程
                    kind, hint = classify_error(exc)
                    report["sources"][name] = {"ok": False, "kind": kind, "hint": hint, "ms": 0}
            for fut, (pool, host) in h_futs.items():
                try:
                    report["hosts"].setdefault(pool, {})[host] = fut.result()
                except Exception as exc:
                    kind, hint = classify_error(exc)
                    report["hosts"].setdefault(pool, {})[host] = {
                        "host": host, "ok": False, "kind": kind, "hint": hint, "ms": 0}
    finally:
        quiet.restore()

    report["verdict"] = _verdict(report["sources"])
    # unsupported 只是「这家没这个接口」，不是故障，也不该熔断——链上本来就会静默跳过。
    report["skipped"] = [name for name, v in report["sources"].items()
                         if not v.get("ok") and v.get("kind") in ("dns", "dns_hijack")]
    report["elapsed"] = round(time.time() - t0, 2)
    _report(io, report)
    return report


def _report(io: Any, report: dict) -> None:
    """把探测结果压成一两行提示：通全报一句，不通说清谁、为什么、怎么办。"""
    if io is None:
        return
    sources, bad = report["sources"], {k: v for k, v in report["sources"].items() if not v["ok"]}
    n = len(sources)
    skipped = report["skipped"]
    dead_hosts = [h for pool in report["hosts"].values() for h, v in pool.items() if not v["ok"]]
    hint = ""
    if dead_hosts:
        hint = f"，坏节点 {len(dead_hosts)} 个已临时绕开"
    if not bad:
        utils.tell(io, f"  ✓ 数据源连通性正常（{n}/{n}{hint}）")
        return
    for name, v in bad.items():
        if v["kind"] == "dns_hijack":
            utils.tell(io, f"  ⏳ {source_label(name)} DNS 被劫持到保留网段（{v['hint']}），本次跳过")
        elif v["kind"] == "dns":
            utils.tell(io, f"  ⏳ {source_label(name)} 域名解析失败，本次跳过")
        elif v["kind"] == "tls":
            utils.tell(io, f"  ⚠️  {source_label(name)} 证书校验失败，检查 Python CA（certifi）")
        elif v["kind"] == "unsupported":
            continue
        else:
            utils.tell(io, f"  ⏳ {source_label(name)} 探测失败（{v['hint']}），本次跳过")
    if report["verdict"] == "down":
        utils.tell(io, f"  ⚠️  数据源大面积不可达（{n} 家中 {len(bad)} 家失败），"
                       f"疑似网络/DNS 问题{hint}")
    elif report["verdict"] == "partial":
        utils.tell(io, f"  · 数据源 {n - len(bad)}/{n} 可用{hint}，跳过 {len(skipped)} 家")


def apply_probe(fetcher: Any, chain: Any, report: dict) -> None:
    """把探测结论落地到本次会话：坏源熔断、坏节点排后。"""
    if not report:
        return
    for name in report.get("skipped") or []:
        for p in getattr(chain, "providers", None) or []:
            if p.name != name:
                continue
            for method in _METHODS:
                if chain._supports(p, method):
                    chain._circuit_open.add((name, method))
            _LOG.warning("探测跳过数据源 name=%s reason=%s",
                         name, (report["sources"].get(name) or {}).get("hint"))
    for pool in (report.get("hosts") or {}).values():
        for host, v in pool.items():
            if not v.get("ok"):
                fetcher._dead_hosts.add(host)


def probe_and_apply(cfg: Config, market: Any, io: Any = None) -> dict:
    """采集入口用的一步式：检查开关 → 探测 → 落地 → 把探测请求从采集统计里剔除。

    探测本身要花请求，但它不是「采集开销」，混进收尾那行「网络请求 N 次｜失败 M 次」
    只会让用户以为采集又挂了。所以探完把计数器退回探测前的值。
    """
    fetcher = getattr(market, "f", None)
    chain = getattr(market, "provider", None)
    if fetcher is None or chain is None:
        return {}
    if not bool(cfg.get("market.probe_enabled", True)) or getattr(fetcher, "offline", False):
        return {}
    if not getattr(chain, "providers", None):
        return {}
    before = {k: fetcher.stats.get(k, 0) for k in ("requests", "errors", "total_sec")}
    log_len = len(getattr(fetcher, "log", []) or [])
    try:
        report = probe_providers(cfg, fetcher, io=io, chain=chain)
    except Exception as exc:  # 探测是锦上添花，失败就照常采集
        _LOG.warning("采集前探测自身异常，忽略：%s", exc)
        return {}
    finally:
        for k, v in before.items():
            fetcher.stats[k] = v
        if hasattr(fetcher, "log"):
            del fetcher.log[log_len:]
    apply_probe(fetcher, chain, report)
    _LOG.info("采集前探测 verdict=%s skipped=%s elapsed=%.2fs",
              report.get("verdict"), ",".join(report.get("skipped") or []) or "-",
              report.get("elapsed") or 0.0)
    return report
