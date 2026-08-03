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

    #: 东财 clist 单页硬上限。pz 填 1000 或 6000 不会报错，只是默默地只回 100 行；
    #: 叠上 po=1&fid=f3（按涨幅降序），拿到的就是涨幅榜前 100 名——这是最坑的
    #: 一种默认值：数据看上去完整，实际是个极端偏样本。幸好 data.total 报的是真值。
    CLIST_PAGE = 100

    #: 板块磁盘缓存格式版。翻页修好前落盘的缓存只有 300 个板块，24h 内会被
    #: 直接读回来把修复盖住，所以用版本号作废。
    SECTOR_CACHE_VER = 2

    def __init__(self, cfg: Optional[Config] = None, fetcher: Optional[Fetcher] = None):
        self.cfg = cfg or load_config()
        self.f = fetcher or Fetcher(self.cfg)
        self.cache = MemCache()

    # -------------------------------------------------- clist 翻页
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
        # 请求带的是 fltt=2，东财在这个参数下返回的已经是最终浮点数（f43=1350.6、
        # f170=-0.82），不是放大 100 倍的整数——那是 fltt=1 的形式。所以这里一律
        # 不再缩放，否则价格会小 100 倍，涨幅会小 100 倍。
        g = lambda k: utils.to_float(d.get(k))
        price = g("f43")
        chg_pct = g("f170")
        chg_amt = g("f169")
        pre_close = None
        if price is not None and chg_amt is not None:
            pre_close = round(price - chg_amt, 4)
        name = d.get("f58") or ""
        return {
            "code": code,
            "mkt": mkt,
            "name": name,
            "price": price,
            "high": g("f44"),
            "low": g("f45"),
            "open": g("f46"),
            "volume": g("f47"),
            "amount_yi": (g("f48") / 1e8) if g("f48") is not None else None,
            "vol_ratio": g("f50"),
            "turnover": g("f168"),
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
        # 点位/涨跌幅优先走 quote 池（push2，有 push2delay 兜底）。
        point = chg = None
        quote_err: Optional[Exception] = None
        try:
            js = self.f.get_json(self.cfg.get("market.quote_url"), {
                "secid": secid, "fields": "f43,f170", "invt": 2, "fltt": 2,
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            }, host_pool="cdn_hosts_quote")
            d = js.get("data") or {}
            # 同 _parse_quote：fltt=2 已是最终值。除以 100 会把上证 3832 点算成 38 点，
            # 而 MA20 来自 K 线（本来就是真实点位），于是 ma20_above 恒为 False，
            # 「上证在 MA20 下方」这条门禁会永久锁死新开仓。
            point = utils.to_float(d.get("f43"))
            chg = utils.to_float(d.get("f170"))
        except MarketError as exc:
            quote_err = exc
        # MA20 走 kline 池（push2his 那族，常被单独封）。两条不是一根绳，且 K 线能兼
        # 做点位/涨跌幅的备用源：quote 整条挂了时，用最后一根收盘当点位、与前收比出
        # 涨跌幅——两路独立取源，「当日上证价」绝大多数情况都能显示出来。
        ma20 = None
        kl: List[dict] = []
        try:
            kl = self.get_klines("", limit=int(self.cfg.get("market.index_kline_limit", 25)), secid=secid)
            ma20 = ma(kl, 20)
        except MarketError:
            kl = []
        if point is None or chg is None:
            closes = [r.get("close") for r in kl if r.get("close") is not None]
            if point is None and closes:
                point = closes[-1]
            if chg is None and len(closes) >= 2 and closes[-2]:
                chg = round((closes[-1] / closes[-2] - 1) * 100, 2)
        # 两路都拿不到点位才算真失败：抛错让上层记「数据缺口」并显示「上证 —」。
        if point is None:
            raise quote_err or MarketError("指数点位取数失败")
        out = {
            "point": point,
            "chg_pct": chg,
            "ma20": ma20,
            "ma20_above": (point is not None and ma20 is not None and point > ma20),
        }
        return self.cache.put(key, out)

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
