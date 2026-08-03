"""HTTP 抓取与防封策略。

这一层只关心"把 JSON 稳定地取回来"，不理解任何行情语义：
随机 UA / Referer 轮换、请求间延时抖动、代理池轮换、CDN 节点轮换、失败指数退避。
"""
from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
from typing import Optional

from .. import utils
from ..config_store import Config
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
        self._last_req = 0.0
        self._proxy_idx = 0
        self._sess = requests.Session() if requests else None
        # 东财是国内站。shell 里为翻墙配的 http_proxy/https_proxy 会被 requests 默认
        # 读走，把域名请求硬塞进代理——代理一连不上就 ProxyError 全崩。除非用户显式
        # 开 use_env_proxy 或自己配了 proxy_pool，否则一律直连，不理会环境里的代理。
        if self._sess is not None and not self.use_env_proxy:
            self._sess.trust_env = False
        self.stats = {"requests": 0, "errors": 0, "cache_hits": 0}

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

    def rotate_host(self, url: str, pool_key: str) -> str:
        """CDN 节点轮换：替换 URL 的 host。"""
        pool = self.cfg.get(f"market.{pool_key}") or []
        if not (self.rotate_cdn and pool):
            return url
        parts = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit(parts._replace(netloc=random.choice(pool)))

    def _throttle(self) -> None:
        wait = utils.jitter(self.delay_base, self.delay_spread) - (time.time() - self._last_req)
        if wait > 0:
            time.sleep(wait)

    # -------------------------------------------------- 请求
    def get_json(self, url: str, params: dict, host_pool: Optional[str] = None) -> dict:
        if self.offline:
            raise MarketError("离线模式已开启，禁止发起网络请求")
        last_err: Optional[Exception] = None
        for attempt in range(self.retries):
            target = self.rotate_host(url, host_pool) if host_pool else url
            full = target + ("&" if "?" in target else "?") + urllib.parse.urlencode(params)
            self._throttle()
            self.stats["requests"] += 1
            try:
                raw = self._do_get(full)
                self._last_req = time.time()
                return json.loads(raw) if raw else {}
            except Exception as exc:  # 网络/解析异常统一退避重试
                last_err = exc
                self.stats["errors"] += 1
                self._last_req = time.time()
                time.sleep(self.delay_after_error * (self.backoff ** attempt))
        raise MarketError(f"请求失败({self.retries}次): {url} -> {last_err}")

    def _do_get(self, full_url: str) -> str:
        proxy = self._next_proxy()
        if self._sess is not None:
            pr = {"http": proxy, "https": proxy} if proxy else None
            resp = self._sess.get(full_url, headers=self._headers(), timeout=self.timeout, proxies=pr)
            resp.raise_for_status()
            return resp.text
        req = urllib.request.Request(full_url, headers=self._headers())
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        elif self.use_env_proxy:
            handler = None  # 用默认 opener，读环境变量里的代理
        else:
            handler = urllib.request.ProxyHandler({})  # 空字典＝显式禁用代理，别读环境变量
        opener = urllib.request.build_opener(handler) if handler is not None else urllib.request.build_opener()
        with opener.open(req, timeout=self.timeout) as r:
            return r.read().decode("utf-8", "ignore")
