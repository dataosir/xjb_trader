"""进程内 TTL 缓存。

只做"存入时间 + 取用时按 ttl 判活"，不做容量淘汰——单次运行生命周期很短，
键的数量级是几十个，不值得引入 LRU。
"""
from __future__ import annotations

import time
from typing import Any, Dict


class MemCache:
    def __init__(self):
        self._d: Dict[str, tuple] = {}

    def get(self, key: str, ttl: float) -> Any:
        item = self._d.get(key)
        if not item:
            return None
        ts, val = item
        if ttl > 0 and (time.time() - ts) > ttl:
            return None
        return val

    def put(self, key: str, val: Any) -> Any:
        self._d[key] = (time.time(), val)
        return val

    def clear(self) -> None:
        self._d.clear()
