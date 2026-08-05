"""进程内 TTL 缓存。

只做"存入时间 + 取用时按 ttl 判活"，不做容量淘汰——单次运行生命周期很短，
键的数量级是几十个，不值得引入 LRU。

行情抓取走线程池并发，同一个实例会被多线程读写，故用 RLock 保护字典操作。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict


class MemCache:
    def __init__(self):
        self._d: Dict[str, tuple] = {}
        self._lock = threading.RLock()

    def get(self, key: str, ttl: float) -> Any:
        with self._lock:
            item = self._d.get(key)
            if not item:
                return None
            ts, val = item
            if ttl > 0 and (time.time() - ts) > ttl:
                return None
            return val

    def put(self, key: str, val: Any) -> Any:
        with self._lock:
            self._d[key] = (time.time(), val)
            return val

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
