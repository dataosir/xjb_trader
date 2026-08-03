"""HTTP 抓取与防封策略。

这一层只关心"把响应稳定地取回来"，不理解任何行情语义：
随机 UA / Referer 轮换、请求间延时抖动、代理池轮换、CDN 节点轮换、失败指数退避。

多数据源接进来之后还要管两件小事：per-request 编码（腾讯 GBK / 新浪 GB2312）与
per-request 请求头（新浪的 Referer 是硬性要求），见 `get_text`。
"""
from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from tea.config.config_store import Config
from tea.core import utils
from .errors import MarketError

try:  # requests 可用则优先（连接复用），否则回退 urllib
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None


class Fetcher:
    """带防封策略的 JSON 抓取器。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        m = cfg.data.get("market", {})
        self.timeout = float(m.get("timeout", 8.0))
        self.retries = int(m.get("retries", 3))
        self.backoff = float(m.get("retry_backoff", 1.7))
        self.delay_base = float(m.get("delay_base", 0.35))
        self.delay_spread = float(m.get("delay_spread", 0.25))
        self.delay_after_error = float(m.get("delay_after_error", 1.2))
        self.uas = list(m.get("user_agents") or [])
        self.referers = list(m.get("referers") or [])
        self.proxies = list(m.get("proxy_pool") or [])
        self.rotate_ua = bool(m.get("rotate_ua", True))
        self.rotate_referer = bool(m.get("rotate_referer", True))
        self.rotate_cdn = bool(m.get("rotate_cdn", True))
        self.proxy_rotate = bool(m.get("proxy_rotate", True))
        self.use_env_proxy = bool(m.get("use_env_proxy", False))
        self.offline = bool(m.get("offline", False))
        self.show_progress = bool(m.get("show_progress", True))
        # 兜底提示的最小间隔（秒）：三路并发 / 种子扫描时几个取数点会同时撞墙，
        # 按时间节流把爆发收敛成偶尔一声。
        self.retry_notice_gap = float(m.get("retry_notice_gap_sec", 2.5))
        self.log_cap = int(m.get("request_log_cap", 200))
        self._last_req = 0.0
        self._last_retry_notice = 0.0
        self._proxy_idx = 0
        self._dead_hosts: set = set()  # 本次会话里连接刚敲不开的 CDN 节点，下次排到最后试
        # pool_key → 上次成功的 host：同 URL 高频请求少走弯路（节点死亡时清掉偏好）
        self._preferred_host: dict = {}
        self._sess = requests.Session() if requests else None
        # 东财是国内站。shell 里为翻墙配的 http_proxy/https_proxy 会被 requests 默认
        # 读走，把域名请求硬塞进代理——代理一连不上就 ProxyError 全崩。除非用户显式
        # 开 use_env_proxy 或自己配了 proxy_pool，否则一律直连，不理会环境里的代理。
        if self._sess is not None and not self.use_env_proxy:
            self._sess.trust_env = False
        self.stats = {"requests": 0, "errors": 0, "cache_hits": 0, "total_sec": 0.0}
        #: 每次请求的耗时与结果（只留最近 log_cap 条），给后续调优留数据。
        self.log: list = []

    # -------------------------------------------------- 防封细节
    def _headers(self) -> dict:
        h = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        h["User-Agent"] = random.choice(self.uas) if (self.rotate_ua and self.uas) else (
            self.uas[0] if self.uas else "Mozilla/5.0")
        if self.referers:
            h["Referer"] = random.choice(self.referers) if self.rotate_referer else self.referers[0]
        return h

    def _next_proxy(self) -> Optional[str]:
        if not self.proxies:
            return None
        if self.proxy_rotate:
            self._proxy_idx = (self._proxy_idx + 1) % len(self.proxies)
            return self.proxies[self._proxy_idx]
        return self.proxies[0]

    def _swap_host(self, url: str, host: str) -> str:
        parts = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit(parts._replace(netloc=host))

    def _host_hint(self, url: str, hosts: list) -> str:
        """兜底提示里的「是哪个域名在抖」：轮过节点池就报最后试的那个，否则报 URL 自带的。"""
        tried = [h for h in hosts if h]
        return tried[-1] if tried else (urllib.parse.urlsplit(url).netloc or url)

    def _host_order(self, pool_key: Optional[str]) -> list:
        """给出本次请求要依次尝试的 CDN 节点顺序（None 表示不换 host）。

        旧实现每次 `random.choice` 可能三次重试全撑在同一个坏节点上，而且
        不记事——上一个请求刚确认 push2 死了，下一个又去撞。现在先活后死排序，
        并让同一次 get_json 的几次重试走不同节点，最大化撞上好节点的概率。
        多请求操作（如涨跌家数二分需 ~9 次）最受益。
        """
        pool = list(self.cfg.get(f"market.{pool_key}") or []) if pool_key else []
        if not (self.rotate_cdn and pool):
            return [None]
        random.shuffle(pool)
        pool.sort(key=lambda h: h in self._dead_hosts)  # 活（False）在前，死（True）在后
        # 上次成功的节点若仍活着，顶到最前：同 pool_key 的高频请求（涨跌家数二分
        # ~9 次）少走弯路，不必每次重新撞运气。
        pref = self._preferred_host.get(pool_key) if pool_key else None
        if pref and pref in pool and pref not in self._dead_hosts:
            pool.remove(pref)
            pool.insert(0, pref)
        return pool

    def _throttle(self) -> None:
        wait = utils.jitter(self.delay_base, self.delay_spread) - (time.time() - self._last_req)
        if wait > 0:
            time.sleep(wait)

    # -------------------------------------------------- 耗时统计
    def _record(self, url: str, host: Optional[str], elapsed: float, ok: bool) -> None:
        """记下单次请求的耗时与结果（超过 log_cap 条丢最旧的）。"""
        self.stats["total_sec"] = round(self.stats["total_sec"] + elapsed, 3)
        # 存路径而不是末段：报价/K 线/列表三个接口末段都叫 get，分不出谁慢。
        self.log.append({"path": urllib.parse.urlsplit(url).path or url, "host": host,
                         "sec": round(elapsed, 3), "ok": ok})
        if len(self.log) > self.log_cap:
            del self.log[:-self.log_cap]

    def stats_line(self) -> str:
        """一行网络开销摘要（给长流程收尾用）。零请求时返回空串，调用方判空跳过。"""
        s = self.stats
        if not s.get("requests"):
            return ""
        return (f"网络请求 {s['requests']} 次（失败 {s['errors']}，"
                f"缓存命中 {s['cache_hits']}，累计 {s['total_sec']:.1f}s）")

    @contextmanager
    def with_timeout(self, seconds: Optional[float]) -> Iterator[None]:
        """临时替换超时（多源降级链按源借用，结束即还原）。

        慢源不该按最慢那家的预算白等：链上后面还有几家，早失败早降级。
        """
        old = self.timeout
        self.timeout = float(seconds) if seconds else old
        try:
            yield
        finally:
            self.timeout = old

    @contextmanager
    def with_retries(self, times: Optional[int]) -> Iterator[None]:
        """临时替换重试次数（结束即还原）。

        有的取数点是「成百上千次循环里的一环」（扫 30 个板块的成分股，每个
        板块又要翻多页），而且就东财一家有、无家可降。东财整体不可用时每一环
        都死磕到顶，总耗时就从几十秒满到几分钟——这种地方单独把重试压得更低。
        """
        old = self.retries
        self.retries = max(1, int(times)) if times else old
        try:
            yield
        finally:
            self.retries = old

    # -------------------------------------------------- 请求
    def get_json(self, url: str, params: dict, host_pool: Optional[str] = None) -> dict:
        """取 JSON。解析失败与网络失败一样进重试（半截响应重试一次常能救回）。"""
        return self._request(url, params, host_pool,
                            lambda raw: (json.loads(raw) if raw else {}))

    def get_text(self, url: str, params: Optional[dict] = None,
                 host_pool: Optional[str] = None, encoding: Optional[str] = None,
                 extra_headers: Optional[dict] = None) -> str:
        """取原始文本：腾讯/新浪回的是 JS 变量而非 JSON，得自己解析。

        encoding 显式指定时按它解码（腾讯 gbk、新浪 gb2312）；留空则沿用默认路径，
        与接多源之前完全一致。extra_headers 覆盖轮换出来的头（新浪 Referer）。
        """
        return self._request(url, params or {}, host_pool, lambda raw: raw,
                            encoding, extra_headers)

    def _request(self, url: str, params: dict, host_pool: Optional[str],
                 parse: Callable[[str], Any], encoding: Optional[str] = None,
                 extra_headers: Optional[dict] = None) -> Any:
        if self.offline:
            raise MarketError("离线模式已开启，禁止发起网络请求")
        hosts = self._host_order(host_pool)
        # 单次请求就按 retries 死磕这么多次，不再为「把节点池轮完」而加码：节点池是
        # 同一家源的几个 CDN 入口，整池试完的前提是这家源真有救；而降级链上还有四家
        # 备源，广度由链来提供。硬凑 len(hosts) 只会把降级往后拖（kline 池 4 个节点
        # ×8s 超时 = 白等半分钟）。逐次仍换不同节点，覆盖面在 retries 内尽量铺开。
        attempts = max(1, self.retries)
        last_err: Optional[Exception] = None
        for attempt in range(attempts):
            host = hosts[attempt % len(hosts)]  # 逐次换不同节点，而不是反复随机
            target = self._swap_host(url, host) if host else url
            # 空 params 不拼问号：腾讯/新浪把标的写在 URL 里（`?q=sh600519`），
            # 再缀一个裸问号有些节点会 400。
            full = (target if not params else
                    target + ("&" if "?" in target else "?") + urllib.parse.urlencode(params))
            self._throttle()
            self.stats["requests"] += 1
            t0 = time.time()
            try:
                # 默认路径只传 URL：自测里把 _do_get 换成单参假函数验证节点故障转移。
                raw = (self._do_get(full) if encoding is None and not extra_headers
                       else self._do_get(full, encoding=encoding, extra_headers=extra_headers))
                self._last_req = time.time()
                self._record(url, host, self._last_req - t0, True)
                if host:
                    self._dead_hosts.discard(host)  # 这回通了，从黑名单里放出来
                    if host_pool:
                        self._preferred_host[host_pool] = host  # 记住这次成功的节点
                return parse(raw)
            except Exception as exc:  # 网络/解析异常统一退避重试
                last_err = exc
                self.stats["errors"] += 1
                self._last_req = time.time()
                self._record(url, host, self._last_req - t0, False)
                if host:
                    self._dead_hosts.add(host)  # 该节点敲不开，后续请求尽量绕开
                    if host_pool and self._preferred_host.get(host_pool) == host:
                        self._preferred_host.pop(host_pool, None)  # 首选节点死了，清掉偏好
                time.sleep(self.delay_after_error * (self.backoff ** attempt))
        # 一次超时就是 8 秒卡死，不出声的话屏上就是凭空停一分钟。但逐次重试都报
        # 「网络抖动，正在重试」是无信息重复：紧接着上层降级链就会说清「谁挂了、改用谁」
        # （ChainedProvider._notify_fallback），而重试期间用户能做的事没变（等）。
        # 所以只在重试全部用尽、即将把这一家交出去之前吐一句兜底总结，带上是哪个域名、
        # 试了几次；同时兜住「只配了单源、没有降级链」时无人报信的场景。
        if self.show_progress:
            now = time.time()
            if now - self._last_retry_notice >= self.retry_notice_gap:
                self._last_retry_notice = now
                print(f"  ⏳ {self._host_hint(url, hosts)} 网络抖动"
                      f"（{attempts} 次尝试均失败），即将切换...", flush=True)
        raise MarketError(f"请求失败({attempts}次): {url} -> {last_err}")

    def _do_get(self, full_url: str, encoding: Optional[str] = None,
                extra_headers: Optional[dict] = None) -> str:
        proxy = self._next_proxy()
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)
        if self._sess is not None:
            pr = {"http": proxy, "https": proxy} if proxy else None
            resp = self._sess.get(full_url, headers=headers, timeout=self.timeout, proxies=pr)
            resp.raise_for_status()
            if encoding:
                # 这几家不在响应头里报编码，requests 会猜成 ISO-8859-1，中文全成乱码，
                # 名称对不上 ST/涨停判定就跟着错；且默认 strict 解码遇到生僻股名越界的
                # 字节会 UnicodeDecodeError 整条崩。保留 encoding 原意的同时拿 bytes 手动
                # 以 replace 解码，与下面 urllib 回退路径行为一致（U+FFFD 至少留个线索）。
                resp.encoding = encoding
                return resp.content.decode(encoding, errors="replace")
            return resp.text
        req = urllib.request.Request(full_url, headers=headers)
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        elif self.use_env_proxy:
            handler = None  # 用默认 opener，读环境变量里的代理
        else:
            handler = urllib.request.ProxyHandler({})  # 空字典＝显式禁用代理，别读环境变量
        opener = urllib.request.build_opener(handler) if handler is not None else urllib.request.build_opener()
        with opener.open(req, timeout=self.timeout) as r:
            return r.read().decode(encoding or "utf-8", "replace")
