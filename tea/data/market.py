"""行情门面：把抓取层的原始响应翻译成引擎使用的领域字段。

报价 / 日 K / 指标 / 板块排名 / 板块成分 / 大盘 / 涨跌家数 / 涨停池 / 个股板块上下文。
缓存策略：行情 20s、K 线 300s、板块 600s（内存），板块排名另有 24h 磁盘缓存。

报价 / 日 K / 大盘快照三项走 `providers` 里的降级链（东财→腾讯→新浪→网易→凤凰，
具体启用哪几家看 `market.data_sources`）；板块排名 / 板块成分 / 涨跌家数 / 涨停池
仍直连东财——那四项别家没有对等接口，包成 provider 也无从降级。
缓存在门面这一层，与数据由谁供无关：各源返回的 schema 完全一致。
"""
from __future__ import annotations

import time
from contextlib import nullcontext
from typing import Any, ContextManager, List, Optional

from tea.config.config_store import Config, load_config
from tea.core import utils
from .cache import MemCache
from .errors import MarketError
from .fetcher import Fetcher
from .indicators import compute_indicators, count_limit_ups
from .providers import IDataProvider, build_provider
from .providers.eastmoney import parse_quote as _parse_em_quote


class Market:
    """行情门面：报价 / K 线 / 指标 / 板块 / 大盘 / 涨跌家数 / 涨停池。"""

    #: 东财 clist 单页硬上限。pz 填 1000 或 6000 不会报错，只是默默地只回 100 行；
    #: 叠上 po=1&fid=f3（按涨幅降序），拿到的就是涨幅榜前 100 名——这是最坑的
    #: 一种默认值：数据看上去完整，实际是个极端偏样本。幸好 data.total 报的是真值。
    CLIST_PAGE = 100

    #: 板块磁盘缓存格式版。翻页修好前落盘的缓存只有 300 个板块，24h 内会被
    #: 直接读回来把修复盖住，所以用版本号作废。
    SECTOR_CACHE_VER = 2

    def __init__(self, cfg: Optional[Config] = None, fetcher: Optional[Fetcher] = None,
                 provider: Optional[IDataProvider] = None):
        self.cfg = cfg or load_config()
        self.f = fetcher or Fetcher(self.cfg)
        self.cache = MemCache()
        self.provider = provider or build_provider(self.cfg, self.f)

    # -------------------------------------------------- clist 翻页
    def _retry_scope(self, key: str, default: int) -> "ContextManager[Any]":
        """借用抓取器的重试次数说法（假抓取器没这能力时退化成空操作）。"""
        scope = getattr(self.f, "with_retries", None)
        if scope is None:
            return nullcontext()
        return scope(int(self.cfg.get(f"market.{key}", default) or default))

    def stats_line(self) -> str:
        """一行网络摘要：能报源命中就报，否则回落抓取器的统计行。

        “东财 45｜腾讯 18”比“失败 12 次”更能回答用户的真问题：降级链到底有没接上。
        """
        line = getattr(self.provider, "provider_stats_line", None)
        if callable(line):
            return line()
        return self.f.stats_line() if hasattr(self.f, "stats_line") else ""

    def _clist_page(self, base: dict, pn: int) -> tuple:
        """取 clist 的第 pn 页，返回（行列表, 接口自报的匹配总数）。"""
        js = self.f.get_json(self.cfg.get("market.clist_url"),
                             {**base, "pn": pn, "pz": self.CLIST_PAGE},
                             host_pool="cdn_hosts_quote")
        data = js.get("data") or {}
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        return list(diff), int(utils.to_float(data.get("total"), 0) or 0)

    def _clist_all(self, base: dict, max_pages: int) -> list:
        """按 data.total 翻完整个列表（上限 max_pages 页）。"""
        rows, total = self._clist_page(base, 1)
        if not rows:
            return []
        pages = max(1, -(-total // self.CLIST_PAGE)) if total > 0 else 1
        for pn in range(2, min(pages, max(1, max_pages)) + 1):
            more, _ = self._clist_page(base, pn)
            if not more:
                break
            rows.extend(more)
        return rows

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
        return self.cache.put(key, self.provider.fetch_quote(code))

    @staticmethod
    def _parse_quote(code: str, mkt: int, d: dict) -> dict:
        """东财报价解析（实现已搬到 providers.eastmoney，这里留作对外入口）。"""
        return _parse_em_quote(code, mkt, d)

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
        # secid 一路传下去：指数只有它分得清市场（上证 1.000001 vs 深市 0.000001）。
        return self.cache.put(key, self.provider.fetch_klines(code, limit=lmt, secid=sid))

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
        pages = int(self.cfg.get("market.sector_max_pages", 12))
        rows = self._clist_all({
            "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fs": self.cfg.get("market.sector_fs"),
            "fields": self.cfg.get("market.sector_fields"),
            "fid": "f3", "ut": "b2884a393a59ad64002292a3e90d46a5",
        }, pages)
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
        if not js or int(utils.to_float(js.get("ver"), 0) or 0) != self.SECTOR_CACHE_VER:
            return None
        age_h = (time.time() - float(js.get("ts", 0))) / 3600.0
        if age_h > float(self.cfg.get("market.sector_disk_cache_hours", 24)):
            return None
        return js.get("sectors") or None

    def _sector_disk_save(self, sectors: List[dict]) -> None:
        utils.write_json(self.cfg.data_file("sector_cache_file"),
                         {"ver": self.SECTOR_CACHE_VER, "ts": time.time(),
                          "date": utils.today_str(), "sectors": sectors})

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
        # 翻完整个板块。只取首页 100 只时，大板块（「机械设备」613 只）拿到的是涨幅
        # 前 100 名，而种子扫描要找的正是 3.0~5.5% 的温和票——它们全在前 100 名之外。
        with self._retry_scope("member_retries", 2):
            diff = self._clist_all({
                "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fs": f"b:{bk}", "fields": self.cfg.get("market.member_fields"),
                "fid": "f3", "ut": "b2884a393a59ad64002292a3e90d46a5",
            }, int(self.cfg.get("market.member_max_pages", 10)))
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
        # 每家源内部都是两路取源：点位/涨跌幅走报价，MA20 走 K 线，报价整条挂了就
        # 用最后一根收盘反推点位（见 providers.base.index_double_route）。两路都拿不到
        # 点位才抛错，让上层记「数据缺口」并显示「上证 —」。
        return self.cache.put(key, self.provider.fetch_index_snapshot(secid))

    # -------------------------------------------------- 3.6 涨跌家数
    def get_breadth(self) -> dict:
        """全市场涨跌家数。

        全市场五千多只，按 100 行/页翻完要 56 次请求，与防封目标相冲。但列表是
        按涨幅降序的，「涨幅 > 阈值」就是一段前缀，于是二分定位到跨界的那一页、
        页内再线性数一遍，得到的是**精确值**，约 8~11 次请求。
        """
        key = "breadth"
        ttl = float(self.cfg.get("market.breadth_cache_sec", 120))
        hit = self.cache.get(key, ttl)
        if hit:
            self.f.stats["cache_hits"] += 1
            return hit
        base = {
            "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
            "fs": self.cfg.get("market.breadth_fs"), "fields": "f12,f3",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        eps = float(self.cfg.get("market.breadth_flat_eps", 0.05))
        budget = [int(self.cfg.get("market.breadth_max_probes", 24))]
        truncated = []
        pages: dict = {}
        reported = [0]

        def chgs_of(pn: int) -> list:
            if pn not in pages:
                if budget[0] <= 0:
                    truncated.append(pn)
                    return []
                budget[0] -= 1
                rows, tot = self._clist_page(base, pn)
                pages[pn] = [utils.to_float(r.get("f3")) for r in rows
                             if utils.to_float(r.get("f3")) is not None]
                reported[0] = max(reported[0], tot)
            return pages[pn]

        head = chgs_of(1)
        if not head:
            raise MarketError("涨跌家数为空")
        total = reported[0] or len(head)
        last_page = max(1, -(-total // self.CLIST_PAGE))

        def count_above(thr: float) -> int:
            """数出涨幅 > thr 的股票个数（列表降序，谓词单调，可二分）。"""
            lo, hi, target = 1, last_page, None
            while lo <= hi:
                mid = (lo + hi) // 2
                page = chgs_of(mid)
                if not page:                      # 空页或探测预算用尽
                    hi = mid - 1
                    continue
                if page[-1] <= thr:
                    target, hi = mid, mid - 1     # 跨界就在这页或更前
                else:
                    lo = mid + 1
            if target is None:                    # 每一页末行都还 > thr
                return total
            page = chgs_of(target)
            return (target - 1) * self.CLIST_PAGE + sum(1 for c in page if c > thr)

        rising = count_above(eps)
        # 下跌的定义是 chg < -eps，所以这里取 chg >= -eps 的个数（阀值往下碰一点，
        # 否则恰好等于 -0.05 的会被归成下跌）。
        not_falling = count_above(-eps - 1e-9)
        falling = max(0, total - not_falling)
        denom = rising + falling
        out = {
            "rising": rising,
            "falling": falling,
            "flat": max(0, total - rising - falling),
            "total": total,
            "advance_ratio": (rising / denom) if denom else None,
            "exact": not truncated,
        }
        return self.cache.put(key, out)

    # -------------------------------------------------- 3.7 涨停池
    def get_limit_up_stats(self, date: Optional[str] = None) -> dict:
        """涨停池统计。未指定日期且当日为空时，回退到上一个交易日。

        涨停池是唯一按日期取的数据源，指数 / 板块 / 涨跌比都会自动给上一交易日。
        不回退的话，情绪公式会拿「涨停 0 家」去和上一交易日的板块涨幅做加减，
        算出来的周期是假的——非交易日看到的「涨停 0 家 + 前5板块均涨 10%」就是这么来的。
        """
        want = date
        d = date or utils.compact_date()
        tries = 1 if want else (1 + max(0, int(self.cfg.get("market.ztpool_fallback_days", 3))))
        asked = d
        out = self._ztpool_once(d)
        for _ in range(tries - 1):
            # 取数失败就不必往前翻了：换日子治不了坏令牌，只是白打四次请求。
            if not out["ok"] or out["limit_up_count"]:
                break
            cur = utils.parse_date(d) or utils.now().date()
            d = utils.compact_date(utils.prev_trading_day(cur))
            out = self._ztpool_once(d)
        # 得另拼一份：_ztpool_once 返回的是缓存对象，直接改它会把 fallback
        # 标记写回缓存，下一次同日请求就读到错的。
        return {**out, "fallback": out["date"] != asked}

    def _ztpool_once(self, d: str) -> dict:
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
        data = js.get("data")
        # rc非 0（常见是 ut 令牌不对，回 rc=205 且 data=null）不等于「今天没有涨停」。
        # 混为一谈的后果很重：情绪公式会拿 0 板扣 10 分，还可能触发冰点降仓把
        # 仓位砍到四分之一——一个取数失败悄悄变成了减仓指令。这里让它返回 None，
        # 情绪公式遇到 None 会直接跳过这一项，屏上另行提示数据缺口。
        if int(utils.to_float(js.get("rc"), 0) or 0) != 0 or not isinstance(data, dict):
            return self.cache.put(key, {
                "limit_up_count": None, "max_boards": None, "date": d, "ok": False,
                "error": f"涨停池取数失败 rc={js.get('rc')}（ut 令牌可能已失效）",
            })
        pool = data.get("pool") or []
        boards = [int(utils.to_float(x.get("lbc"), 1) or 1) for x in pool]
        # 家数以接口自报的 tc 为准：pool 被 pagesize 截断时 len(pool) 会偏小。
        tc = utils.to_float(data.get("tc"))
        out = {
            "limit_up_count": int(tc) if tc is not None else len(pool),
            "max_boards": max(boards) if boards else 0,
            "date": d,
            "ok": True,
            "error": None,
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
