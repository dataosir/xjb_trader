"""行情门面：把抓取层的原始 JSON 翻译成引擎使用的领域字段。

报价 / 日 K / 指标 / 板块排名 / 板块成分 / 大盘 / 涨跌家数 / 涨停池 / 个股板块上下文。
缓存策略：行情 20s、K 线 300s、板块 600s（内存），板块排名另有 24h 磁盘缓存。
"""
from __future__ import annotations

import time
from typing import List, Optional

from .. import utils
from ..config_store import Config, load_config
from .cache import MemCache
from .errors import MarketError
from .fetcher import Fetcher
from .indicators import compute_indicators, count_limit_ups, ma


class Market:
    """行情门面：报价 / K 线 / 指标 / 板块 / 大盘 / 涨跌家数 / 涨停池。"""

    def __init__(self, cfg: Optional[Config] = None, fetcher: Optional[Fetcher] = None):
        self.cfg = cfg or load_config()
        self.f = fetcher or Fetcher(self.cfg)
        self.cache = MemCache()

    # -------------------------------------------------- 3.1 实时行情
    def get_quote(self, code: str) -> dict:
        code = utils.norm_code(code)
        if len(code) != 6:
            raise MarketError(f"非法股票代码: {code}")
        ttl = float(self.cfg.get("market.quote_cache_sec", 20))
        key = f"quote:{code}"
        hit = self.cache.get(key, ttl)
        if hit:
            self.f.stats["cache_hits"] += 1
            return hit
        mkt = utils.market_of(code)
        data = self.f.get_json(self.cfg.get("market.quote_url"), {
            "secid": f"{mkt}.{code}",
            "fields": self.cfg.get("market.quote_fields"),
            "invt": 2, "fltt": 2, "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }, host_pool="cdn_hosts_quote").get("data") or {}
        if not data:
            raise MarketError(f"行情为空: {code}")
        q = self._parse_quote(code, mkt, data)
        return self.cache.put(key, q)

    @staticmethod
    def _parse_quote(code: str, mkt: int, d: dict) -> dict:
        g = lambda k, div=1.0: (utils.to_float(d.get(k)) / div) if utils.to_float(d.get(k)) is not None else None
        price = g("f43", 100.0)
        chg_pct = g("f170", 100.0)
        chg_amt = g("f169", 100.0)
        pre_close = None
        if price is not None and chg_amt is not None:
            pre_close = round(price - chg_amt, 4)
        name = d.get("f58") or ""
        return {
            "code": code,
            "mkt": mkt,
            "name": name,
            "price": price,
            "high": g("f44", 100.0),
            "low": g("f45", 100.0),
            "open": g("f46", 100.0),
            "volume": g("f47"),
            "amount_yi": (g("f48") / 1e8) if g("f48") is not None else None,
            "vol_ratio": g("f50", 100.0),
            "turnover": g("f168", 100.0),
            "chg_pct": chg_pct,
            "chg_amt": chg_amt,
            "pre_close": pre_close,
            "cap_yi": (g("f116") / 1e8) if g("f116") is not None else None,
            "float_cap_yi": (g("f117") / 1e8) if g("f117") is not None else None,
            "industry": d.get("f127") or d.get("f100") or "",
            "board": utils.board_of(code),
            "limit_up_pct": utils.limit_up_pct(code, name),
            "is_st": utils.is_st(name),
            "ts": utils.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # -------------------------------------------------- 3.2 日 K
    def get_klines(self, code: str, limit: Optional[int] = None, secid: Optional[str] = None) -> List[dict]:
        code = utils.norm_code(code) if not secid else code
        lmt = int(limit or self.cfg.get("market.kline_limit", 30))
        sid = secid or f"{utils.market_of(code)}.{code}"
        key = f"kline:{sid}:{lmt}"
        ttl = float(self.cfg.get("market.kline_cache_sec", 300))
        hit = self.cache.get(key, ttl)
        if hit:
            self.f.stats["cache_hits"] += 1
            return hit
        js = self.f.get_json(self.cfg.get("market.kline_url"), {
            "secid": sid,
            "klt": self.cfg.get("market.kline_klt", 101),
            "fqt": self.cfg.get("market.kline_fqt", 1),
            "lmt": lmt,
            "end": "20500101",
            "fields1": self.cfg.get("market.kline_fields1"),
            "fields2": self.cfg.get("market.kline_fields2"),
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }, host_pool="cdn_hosts_kline")
        rows = ((js.get("data") or {}).get("klines") or [])
        out = []
        for line in rows:
            p = str(line).split(",")
            if len(p) < 5:
                continue
            out.append({
                "date": p[0],
                "open": utils.to_float(p[1]),
                "close": utils.to_float(p[2]),
                "high": utils.to_float(p[3]),
                "low": utils.to_float(p[4]),
                "volume": utils.to_float(p[5]) if len(p) > 5 else None,
                "amount": utils.to_float(p[6]) if len(p) > 6 else None,
            })
        return self.cache.put(key, out)

    # -------------------------------------------------- 指标
    def get_indicators(self, code: str, price: Optional[float] = None) -> dict:
        try:
            kl = self.get_klines(code)
        except MarketError:
            kl = []
        return compute_indicators(kl, price)

    # -------------------------------------------------- 3.3 板块排名
    def get_sector_ranking(self, force: bool = False) -> List[dict]:
        key = "sector_rank"
        ttl = float(self.cfg.get("market.sector_cache_sec", 600))
        if not force:
            hit = self.cache.get(key, ttl)
            if hit:
                self.f.stats["cache_hits"] += 1
                return hit
            disk = self._sector_disk_load()
            if disk:
                return self.cache.put(key, disk)
        pages = int(self.cfg.get("market.sector_pages", 3))
        size = int(self.cfg.get("market.sector_page_size", 200))
        rows: List[dict] = []
        for pn in range(1, pages + 1):
            js = self.f.get_json(self.cfg.get("market.clist_url"), {
                "pn": pn, "pz": size, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fs": self.cfg.get("market.sector_fs"),
                "fields": self.cfg.get("market.sector_fields"),
                "fid": "f3", "ut": "b2884a393a59ad64002292a3e90d46a5",
            }, host_pool="cdn_hosts_quote")
            diff = ((js.get("data") or {}).get("diff") or [])
            if isinstance(diff, dict):
                diff = list(diff.values())
            if not diff:
                break
            rows.extend(diff)
        sectors = []
        for d in rows:
            chg = utils.to_float(d.get("f3"))
            if chg is None:
                continue
            sectors.append({
                "bk": str(d.get("f12") or ""),
                "name": d.get("f14") or "",
                "chg": chg,
                "up_n": int(utils.to_float(d.get("f104"), 0) or 0),
                "down_n": int(utils.to_float(d.get("f105"), 0) or 0),
            })
        sectors.sort(key=lambda x: x["chg"], reverse=True)
        for i, s in enumerate(sectors, 1):
            s["rank"] = i
        self._sector_disk_save(sectors)
        return self.cache.put(key, sectors)

    def _sector_disk_load(self) -> Optional[List[dict]]:
        path = self.cfg.data_file("sector_cache_file")
        js = utils.read_json(path)
        if not js:
            return None
        age_h = (time.time() - float(js.get("ts", 0))) / 3600.0
        if age_h > float(self.cfg.get("market.sector_disk_cache_hours", 24)):
            return None
        return js.get("sectors") or None

    def _sector_disk_save(self, sectors: List[dict]) -> None:
        utils.write_json(self.cfg.data_file("sector_cache_file"),
                         {"ts": time.time(), "date": utils.today_str(), "sectors": sectors})

    def find_sector(self, name_or_bk: str) -> Optional[dict]:
        """按板块名（模糊）或 bk 代码查排名条目。"""
        if not name_or_bk:
            return None
        key = str(name_or_bk).strip()
        try:
            sectors = self.get_sector_ranking()
        except MarketError:
            return None
        for s in sectors:
            if s["bk"] == key or s["name"] == key:
                return s
        for s in sectors:
            if key and (key in s["name"] or s["name"] in key):
                return s
        return None

    # -------------------------------------------------- 3.4 板块成分股
    def get_sector_members(self, bk: str) -> List[dict]:
        key = f"members:{bk}"
        ttl = float(self.cfg.get("market.member_cache_sec", 600))
        hit = self.cache.get(key, ttl)
        if hit:
            self.f.stats["cache_hits"] += 1
            return hit
        js = self.f.get_json(self.cfg.get("market.clist_url"), {
            "pn": 1, "pz": int(self.cfg.get("market.member_page_size", 1000)),
            "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fs": f"b:{bk}", "fields": self.cfg.get("market.member_fields"),
            "fid": "f3", "ut": "b2884a393a59ad64002292a3e90d46a5",
        }, host_pool="cdn_hosts_quote")
        diff = ((js.get("data") or {}).get("diff") or [])
        if isinstance(diff, dict):
            diff = list(diff.values())
        out = []
        for d in diff:
            code = utils.norm_code(str(d.get("f12") or ""))
            chg = utils.to_float(d.get("f3"))
            if not code or chg is None:
                continue
            cap = utils.to_float(d.get("f20"))
            out.append({
                "code": code,
                "name": d.get("f14") or "",
                "chg": chg,
                "turnover": utils.to_float(d.get("f8")),
                "cap_yi": (cap / 1e8) if cap is not None else None,
            })
        out.sort(key=lambda x: x["chg"], reverse=True)
        for i, m in enumerate(out, 1):
            m["rank"] = i
        return self.cache.put(key, out)

    # -------------------------------------------------- 3.5 大盘指数
    def get_index(self) -> dict:
        key = "index"
        ttl = float(self.cfg.get("market.index_cache_sec", 120))
        hit = self.cache.get(key, ttl)
        if hit:
            self.f.stats["cache_hits"] += 1
            return hit
        secid = self.cfg.get("market.index_secid", "1.000001")
        js = self.f.get_json(self.cfg.get("market.quote_url"), {
            "secid": secid, "fields": "f43,f170", "invt": 2, "fltt": 2,
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }, host_pool="cdn_hosts_quote")
        d = js.get("data") or {}
        point = utils.safe_div(utils.to_float(d.get("f43")), 100.0)
        chg = utils.safe_div(utils.to_float(d.get("f170")), 100.0)
        kl = self.get_klines("", limit=int(self.cfg.get("market.index_kline_limit", 25)), secid=secid)
        ma20 = ma(kl, 20)
        out = {
            "point": point,
            "chg_pct": chg,
            "ma20": ma20,
            "ma20_above": (point is not None and ma20 is not None and point > ma20),
        }
        return self.cache.put(key, out)

    # -------------------------------------------------- 3.6 涨跌家数
    def get_breadth(self) -> dict:
        key = "breadth"
        ttl = float(self.cfg.get("market.breadth_cache_sec", 120))
        hit = self.cache.get(key, ttl)
        if hit:
            self.f.stats["cache_hits"] += 1
            return hit
        js = self.f.get_json(self.cfg.get("market.clist_url"), {
            "pn": 1, "pz": int(self.cfg.get("market.breadth_page_size", 6000)),
            "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
            "fs": self.cfg.get("market.breadth_fs"), "fields": "f12,f3",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }, host_pool="cdn_hosts_quote")
        diff = ((js.get("data") or {}).get("diff") or [])
        if isinstance(diff, dict):
            diff = list(diff.values())
        eps = float(self.cfg.get("market.breadth_flat_eps", 0.05))
        rising = falling = 0
        for d in diff:
            chg = utils.to_float(d.get("f3"))
            if chg is None:
                continue
            if chg > eps:
                rising += 1
            elif chg < -eps:
                falling += 1
        total = rising + falling
        out = {
            "rising": rising,
            "falling": falling,
            "total": len(diff),
            "advance_ratio": (rising / total) if total else None,
        }
        return self.cache.put(key, out)

    # -------------------------------------------------- 3.7 涨停池
    def get_limit_up_stats(self, date: Optional[str] = None) -> dict:
        d = date or utils.compact_date()
        key = f"ztpool:{d}"
        ttl = float(self.cfg.get("market.ztpool_cache_sec", 120))
        hit = self.cache.get(key, ttl)
        if hit:
            self.f.stats["cache_hits"] += 1
            return hit
        js = self.f.get_json(self.cfg.get("market.ztpool_url"), {
            "ut": self.cfg.get("market.ztpool_ut"),
            "dpt": "wz.ztzt", "Pageindex": 0,
            "pagesize": int(self.cfg.get("market.ztpool_page_size", 300)),
            "sort": "fbt:asc", "date": d,
        })
        pool = ((js.get("data") or {}).get("pool") or []) if js.get("data") else []
        boards = [int(utils.to_float(x.get("lbc"), 1) or 1) for x in pool]
        out = {
            "limit_up_count": len(pool),
            "max_boards": max(boards) if boards else 0,
            "date": d,
        }
        return self.cache.put(key, out)

    # -------------------------------------------------- 板块上下文
    def sector_context(self, quote: dict, prefer: Optional[str] = None) -> dict:
        """解析个股所属板块上下文：板块排名/涨幅/涨停家数/个股板块内排名。"""
        entry = self.find_sector(prefer or quote.get("industry") or "")
        ctx = {
            "found": False, "bk": None, "name": quote.get("industry") or "",
            "rank": None, "total": None, "chg": None, "up_n": None, "down_n": None,
            "limit_up_count": 0, "member_total": None, "stock_rank": None,
            "stock_rank_pct": None, "up_ratio": None, "members": [],
        }
        if not entry:
            return ctx
        try:
            members = self.get_sector_members(entry["bk"])
        except MarketError:
            members = []
        try:
            sector_total = len(self.get_sector_ranking())
        except MarketError:
            sector_total = None
        ctx.update({
            "found": True, "bk": entry["bk"], "name": entry["name"], "rank": entry["rank"],
            "total": sector_total,
            "chg": entry["chg"], "up_n": entry.get("up_n"), "down_n": entry.get("down_n"),
            "members": members, "member_total": len(members) or None,
            "limit_up_count": count_limit_ups(members),
        })
        if members:
            ups = sum(1 for m in members if (m["chg"] or 0) > 0)
            ctx["up_ratio"] = ups / len(members)
            for m in members:
                if m["code"] == quote.get("code"):
                    ctx["stock_rank"] = m["rank"]
                    ctx["stock_rank_pct"] = m["rank"] / len(members)
                    break
        return ctx
