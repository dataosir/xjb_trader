"""selftest.py — 离线自测：不联网，用假行情验证核心公式与门禁是否与规格对齐。

每一项断言都在测试内按规格文档独立重算一遍，再和引擎输出比对，
所以它能抓住"实现和规格分叉"的问题，而不只是"代码没崩"。

    python -m tea selftest
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import io as io_mod
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional

from .analysis import (expectancy as exp_mod, followthrough as ft_mod,
                       identity as ident_mod, sentiment as sent_mod)
from .config import config_store
from .config.config_store import Config
from .core import paths, utils
from .data import Market, MarketError, indicators
from .data.fetcher import Fetcher
from .portfolio import plan as plan_mod, portfolio
from .screening import gates, preflight, screener as screener_mod, seed_report, veto as veto_mod

TARGET = "600123"
TARGET_NAME = "测试光伏"
HOT_BK = "BK0001"
HOT_NAME = "光伏设备"


# ==================================================================== 断言框架

class Suite:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.passed = 0
        self.failed: List[str] = []
        self.section = ""

    def head(self, title: str) -> None:
        self.section = title
        if self.verbose:
            print(f"\n── {title} " + "─" * max(0, 46 - len(title)))

    def ok(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            if self.verbose:
                print(f"  ✓ {name}" + (f"　{detail}" if detail else ""))
        else:
            self.failed.append(f"[{self.section}] {name} — {detail}")
            print(f"  ✗ {name}　{detail}")
        return bool(cond)

    def eq(self, name: str, got: Any, want: Any, tol: Optional[float] = None) -> bool:
        if tol is not None and isinstance(got, (int, float)) and isinstance(want, (int, float)):
            cond = abs(got - want) <= tol
        else:
            cond = got == want
        return self.ok(name, cond, f"got={got} want={want}")

    def report(self) -> int:
        total = self.passed + len(self.failed)
        print("\n" + "=" * 56)
        print(f"自测结果：{self.passed}/{total} 通过")
        for f in self.failed:
            print(f"  ✗ {f}")
        print("=" * 56)
        return 0 if not self.failed else 1


# ==================================================================== 假行情

def _end_dates(n: int) -> List[str]:
    """生成 n 根日K的日期，最后一根落在最近一个交易日（交易日即今天）。

    日期只是 kline 的标签（指标只用 OHLC），不要求每根都是真实交易日；这里用
    「从最近交易日往前数 n 天」，保证最后一根是「今日最近数据」，让
    compute_indicators 的 kline_stale 判定为 False（与真实盘中/盘后一致）。
    """
    end = utils.parse_date(utils.latest_trading_day_str())
    return [(end - _dt.timedelta(days=n - 1 - i)).strftime("%Y-%m-%d") for i in range(n)]


def _klines(n: int = 30, start: float = 10.0, step: float = 0.06,
            half_range: float = 0.008) -> List[dict]:
    """构造温和上行的日 K：MA5≥MA10≥MA20，且 ATR 可算。"""
    dates = _end_dates(n)
    out = []
    for i in range(n):
        close = round(start + step * i, 4)
        out.append({
            "date": dates[i],
            "open": round(close * (1 - half_range / 2), 4),
            "close": close,
            "high": round(close * (1 + half_range), 4),
            "low": round(close * (1 - half_range), 4),
            "volume": 1000000 + i * 1000,
            "amount": 12000000.0,
        })
    return out


def _flat_klines(n: int = 30, level: float = 20.0) -> List[dict]:
    dates = _end_dates(n)
    return [{"date": dates[i], "open": level, "close": level,
             "high": level * 1.005, "low": level * 0.995,
             "volume": 100000, "amount": 1000000.0} for i in range(n)]


def _quote(code: str, name: str, price: float, chg: float, industry: str, **kw) -> dict:
    q = {
        "code": code, "mkt": utils.market_of(code), "name": name, "price": price,
        "high": kw.get("high", round(price * 1.01, 2)),
        "low": kw.get("low", round(price * 0.96, 2)),
        "open": kw.get("open", round(price * 0.97, 2)),
        "volume": 1000000, "amount_yi": kw.get("amount_yi", 15.0),
        "vol_ratio": kw.get("vol_ratio", 1.8), "turnover": kw.get("turnover", 8.5),
        "chg_pct": chg, "chg_amt": round(price * chg / (100 + chg), 4),
        "pre_close": round(price / (1 + chg / 100.0), 4),
        "cap_yi": kw.get("cap_yi", 120.0), "float_cap_yi": kw.get("cap_yi", 120.0),
        "industry": industry, "board": utils.board_of(code),
        "limit_up_pct": utils.limit_up_pct(code, name),
        "is_st": utils.is_st(name), "ts": "2026-08-02 14:10:00",
    }
    return q


SECTORS = [
    {"bk": HOT_BK, "name": HOT_NAME, "chg": 6.50, "up_n": 62, "down_n": 3},
    {"bk": "BK0002", "name": "储能", "chg": 4.20, "up_n": 40, "down_n": 8},
    {"bk": "BK0003", "name": "化工", "chg": 3.60, "up_n": 55, "down_n": 20},
    {"bk": "BK0004", "name": "半导体", "chg": 3.10, "up_n": 60, "down_n": 25},
    {"bk": "BK0005", "name": "汽车零部件", "chg": 3.00, "up_n": 50, "down_n": 22},
    {"bk": "BK0006", "name": "医药", "chg": 2.10, "up_n": 45, "down_n": 30},
    {"bk": "BK0007", "name": "银行", "chg": 0.80, "up_n": 20, "down_n": 12},
    {"bk": "BK0008", "name": "房地产", "chg": -0.50, "up_n": 10, "down_n": 40},
]


def _members(bk: str) -> List[dict]:
    """板块成分股：BK0001 里 2 家涨停 + 目标股排第 3（板块内前 10%）。"""
    if bk == HOT_BK:
        rows = [
            {"code": "600111", "name": "涨停一号", "chg": 9.95, "turnover": 12.0, "cap_yi": 90.0},
            {"code": "600222", "name": "涨停二号", "chg": 9.90, "turnover": 11.0, "cap_yi": 80.0},
            {"code": TARGET, "name": TARGET_NAME, "chg": 5.20, "turnover": 8.5, "cap_yi": 120.0},
        ]
        for i in range(27):
            rows.append({"code": f"6005{i:02d}", "name": f"跟风{i:02d}",
                         "chg": round(2.90 - i * 0.09, 2), "turnover": 5.0,
                         "cap_yi": 70.0 + i})
    elif bk == "BK0007":
        # 19 只填充股涨幅都高于 600999，保证 600999 排在末位（板块内后 50%）
        rows = [{"code": f"6013{i:02d}", "name": f"银行{i:02d}", "chg": round(2.0 - i * 0.04, 2),
                 "turnover": 1.0, "cap_yi": 900.0} for i in range(19)]
        rows.append({"code": "600999", "name": "测试银行", "chg": 1.00,
                     "turnover": 0.8, "cap_yi": 800.0})
    else:
        rows = [{"code": f"6{bk[-1]}{i:04d}", "name": f"{bk}成分{i:02d}",
                 "chg": round(2.50 - i * 0.20, 2), "turnover": 4.0, "cap_yi": 60.0}
                for i in range(12)]
    rows.sort(key=lambda x: x["chg"], reverse=True)
    for i, m in enumerate(rows, 1):
        m["rank"] = i
    return rows


class FakeMarket(Market):
    """离线行情：只覆盖网络方法，指标/板块上下文仍走真实实现。"""

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.quotes: Dict[str, dict] = {
            TARGET: _quote(TARGET, TARGET_NAME, 12.00, 5.20, HOT_NAME,
                           high=12.10, low=11.50, open=11.60),
            "600999": _quote("600999", "测试银行", 20.00, 1.00, "银行",
                             high=20.2, low=19.8, cap_yi=800.0, vol_ratio=0.9,
                             turnover=0.8, amount_yi=3.0),
        }
        self.index = {"point": 3200.0, "chg_pct": 0.85, "ma20": 3150.0, "ma20_above": True,
                      "ma20_bias_pct": 1.59, "ma20_slope_pct": 0.5}
        self.breadth = {"rising": 3000, "falling": 1500, "total": 4800,
                        "advance_ratio": 3000 / 4500}
        self.limit_up = {"limit_up_count": 60, "max_boards": 5, "date": utils.compact_date()}
        self._members_cache: Dict[str, List[dict]] = {}

    # -------------------------------------------------- 覆盖网络方法
    def get_quote(self, code: str) -> dict:
        code = utils.norm_code(code)
        if code in self.quotes:
            return self.quotes[code]
        for s in SECTORS:
            for m in self.get_sector_members(s["bk"]):
                if m["code"] == code:
                    q = _quote(code, m["name"], 15.0, m["chg"], s["name"],
                               cap_yi=m.get("cap_yi") or 60.0,
                               turnover=m.get("turnover") or 4.0)
                    self.quotes[code] = q
                    return q
        q = _quote(code, f"未知{code}", 10.0, 0.0, "")
        self.quotes[code] = q
        return q

    def get_klines(self, code: str, limit: Optional[int] = None,
                   secid: Optional[str] = None) -> List[dict]:
        if secid and secid.endswith("000001"):
            return _flat_klines(25, 3100.0)
        code = utils.norm_code(code or "")
        if code == "600999":
            # 阴跌走势：MA5<MA10<MA20 且现价跌破 MA20 → 触发均线扣分
            return _klines(30, 26.0, -0.20)
        return _klines()

    def get_sector_ranking(self, force: bool = False) -> List[dict]:
        out = [dict(s) for s in sorted(SECTORS, key=lambda x: x["chg"], reverse=True)]
        for i, s in enumerate(out, 1):
            s["rank"] = i
        return out

    def get_sector_members(self, bk: str) -> List[dict]:
        if bk not in self._members_cache:
            self._members_cache[bk] = _members(bk)
        return self._members_cache[bk]

    def get_index(self) -> dict:
        return dict(self.index)

    def get_breadth(self) -> dict:
        return dict(self.breadth)

    def get_limit_up_stats(self, date: Optional[str] = None) -> dict:
        return dict(self.limit_up)


# ==================================================================== 各项检查

def check_raw_parsing(t: Suite, cfg: Config) -> None:
    """用东财真实返回的原始 payload 验证行情解析的量级。

    其余检查全走 FakeMarket，绕过了 _parse_quote / get_index，所以“fltt 缩放
    系数搞错”这类 bug 它们一条也抱不住——曾经真的没抱住。
    下面的 payload 是 fltt=2 实际抓回来的形式：已经是最终浮点数。
    """
    t.head("行情解析 · fltt=2 量级")

    # 贵州茅台某日真实返回
    raw = {"f43": 1350.6, "f44": 1355.72, "f45": 1325.77, "f46": 1330.03,
           "f50": 1.08, "f58": "贵州茅台", "f168": 0.44, "f169": -11.16, "f170": -0.82}
    q = Market._parse_quote("600519", 1, raw)
    t.eq("现价不被缩放", q["price"], 1350.6, tol=1e-6)
    t.eq("涨幅不被缩放", q["chg_pct"], -0.82, tol=1e-6)
    t.eq("最高价", q["high"], 1355.72, tol=1e-6)
    t.eq("量比", q["vol_ratio"], 1.08, tol=1e-6)
    t.eq("换手率", q["turnover"], 0.44, tol=1e-6)

    # 尺度无关的自洽断言：价格和涨幅只要有一边被缩放，这个恒等式就破。
    implied = (q["price"] / q["pre_close"] - 1) * 100
    t.eq("涨幅与昨收自洽", round(implied, 2), q["chg_pct"], tol=0.01)

    # 指数：点位来自报价、MA20 来自 K 线，两边缩放不一致时
    # ma20_above 会恒为 False，把新开仓永久锁死。
    closes = [3800.0] * 20

    class _Stub:
        stats = {"requests": 0, "errors": 0, "cache_hits": 0}

        def get_json(self, url, params, host_pool=None):
            if "klt" in params:  # K 线：日期,开,收,高,低,量,额
                return {"data": {"klines": [f"2026-07-{i + 1:02d},3790,{c},3810,3780,1,2"
                                            for i, c in enumerate(closes)]}}
            return {"data": {"f43": 3832.26, "f170": 0.72}}

    idx = Market(cfg, fetcher=_Stub()).get_index()
    t.eq("指数点位不被缩放", idx["point"], 3832.26, tol=1e-6)
    t.eq("指数涨幅不被缩放", idx["chg_pct"], 0.72, tol=1e-6)
    t.eq("MA20", idx["ma20"], 3800.0, tol=1e-6)
    t.ok("点位与 MA20 同量级", abs(idx["point"] - idx["ma20"]) / idx["ma20"] < 0.5,
         f"point={idx['point']} ma20={idx['ma20']}")
    t.ok("3832 > MA20 3800 → 在上方", idx["ma20_above"] is True)


class _FakeClist:
    """模拟东财 clist：无论 pz 填多少都只回 100 行，但 total 报真值。

    这就是真接口的行为，也正是踩过的坑：请求 pz=6000 不报错，静悄悄只给 100 行；
    叠上按涨幅降序，拿到的是涨幅榜前 100 名——看起来很正常，实际是极端偏样本。
    """

    HARD_CAP = 100

    def __init__(self, chgs: list):
        self.chgs = chgs
        self.calls: list = []
        self.stats = {"requests": 0, "errors": 0, "cache_hits": 0}

    def get_json(self, url, params, host_pool=None):
        self.calls.append(dict(params))
        self.stats["requests"] += 1
        pn = max(1, int(params.get("pn", 1)))
        pz = min(int(params.get("pz", 20)), self.HARD_CAP)
        start = (pn - 1) * pz
        rows = [{"f12": f"{600000 + i:06d}", "f14": f"股{i}", "f3": c,
                 "f8": 3.0, "f20": 1.0e10, "f104": 1, "f105": 1}
                for i, c in enumerate(self.chgs[start:start + pz], start)]
        return {"data": {"total": len(self.chgs), "diff": rows}}


def check_clist_paging(t: Suite, cfg: Config) -> None:
    """clist 翻页：精确涨跌家数 + 板块成分不被截断。

    历史 bug：配置里写 pz=6000 / 1000 / 200，以为一次拿全，实际只拿到 100 行。
    后果是涨跌比恒为 100%（取到的全是涨幅榜前 100 名），以及大板块里 3~5.5%
    的温和票全部看不见——而那正是种子扫描要找的东西。
    """
    t.head("数据层 · clist 翻页与精确涨跌家数")

    # ---- 涨跌家数：5545 只，涨 3000 / 平 45 / 跌 2500（降序）
    rising_n, flat_n, falling_n = 3000, 45, 2500
    chgs = ([round(9.99 - i * 0.003, 4) for i in range(rising_n)]
            + [0.0] * flat_n
            + [round(-0.06 - i * 0.003, 4) for i in range(falling_n)])
    fake = _FakeClist(chgs)
    br = Market(cfg, fetcher=fake).get_breadth()
    t.eq("全市场总数取 data.total", br["total"], rising_n + flat_n + falling_n)
    t.eq("上涨家数精确", br["rising"], rising_n)
    t.eq("下跌家数精确", br["falling"], falling_n)
    t.eq("平盘家数精确", br["flat"], flat_n)
    t.eq("涨跌比", round(br["advance_ratio"], 6),
         round(rising_n / (rising_n + falling_n), 6), tol=1e-6)
    t.ok("标记为精确值", br["exact"] is True)
    # 翻完 5545 只要 56 页；二分定位应该远少于此，否则就是在硬碰接口。
    t.ok("请求数远少于全量翻页", len(fake.calls) <= 15,
         f"实际 {len(fake.calls)} 次（全量需 56 次）")
    t.ok("从不声称 pz>100", all(int(c["pz"]) <= 100 for c in fake.calls),
         f"pz={sorted({int(c['pz']) for c in fake.calls})}")

    # 一个普涨日：前 100 名全是涨的。旧实现在这里会算出 100%。
    fake2 = _FakeClist([round(9.99 - i * 0.002, 4) for i in range(1200)]
                       + [round(-0.5 - i * 0.001, 4) for i in range(800)])
    br2 = Market(cfg, fetcher=fake2).get_breadth()
    t.eq("普涨日不会误报 100%", round(br2["advance_ratio"], 4), round(1200 / 2000, 4), tol=1e-4)

    # ---- 板块成分：613 只，温和票（3~5.5%）全在前 100 名之外
    member_chgs = ([round(10.0 - i * 0.02, 4) for i in range(150)]      # 前 150 名均 >7%
                   + [round(5.4 - i * 0.01, 4) for i in range(240)]     # 5.4% → 3.0%
                   + [round(0.5 - i * 0.002, 4) for i in range(223)])
    fake3 = _FakeClist(member_chgs)
    members = Market(cfg, fetcher=fake3).get_sector_members("BK1205")
    t.eq("板块成分拿全", len(members), len(member_chgs))
    t.eq("末位成分股排名", members[-1]["rank"], len(member_chgs))
    mild = [m for m in members if 3.0 <= (m["chg"] or 0) <= 5.5]
    t.ok("温和票没被首页截掉", len(mild) >= 200, f"温和票 {len(mild)} 只")
    t.ok("温和票确实在前 100 名之外", min(m["rank"] for m in mild) > 100,
         f"最靠前的温和票排 {min(m['rank'] for m in mild)}")

    # ---- 板块排名：437 个，不能停在 300（3 页 × 100）
    fake4 = _FakeClist([round(15.0 - i * 0.03, 4) for i in range(437)])
    sectors = Market(cfg, fetcher=fake4).get_sector_ranking(force=True)
    t.eq("板块排名拿全", len(sectors), 437)
    t.eq("板块总数不是 3×100", len(sectors) % 100 != 0, True)


def check_disk_fallback(t: Suite, cfg: Config) -> None:
    """无备源接口（涨跌家数/涨停池）的磁盘兜底：实时取数失败回退最近成功值。

    东财 clist/ztpool 间歇性 RemoteDisconnected，且无备源。实时失败时回退磁盘
    缓存并标注 stale，避免天气里出现「涨跌比 — / 涨停 —」。
    """
    t.head("数据层 · 涨跌家数/涨停池磁盘兜底")

    class _FailFetcher:
        def __init__(self):
            self.stats = {"requests": 0, "errors": 0, "cache_hits": 0}

        def get_json(self, url, params, host_pool=None):
            self.stats["requests"] += 1
            self.stats["errors"] += 1
            raise MarketError("模拟网络失败")

        def stats_line(self):
            return ""

    # 先落一份兜底缓存（模拟上一次成功取数）
    mk0 = Market(cfg)
    mk0._kv_disk_save("breadth", {"rising": 3000, "falling": 1500, "flat": 45,
                                  "total": 4545, "advance_ratio": 3000 / 4500,
                                  "exact": True})
    mk0._kv_disk_save("ztpool", {"limit_up_count": 55, "max_boards": 5,
                                 "date": "20260818", "ok": True})

    # 实时取数全失败 → 回退兜底缓存并标注 stale
    mk = Market(cfg, fetcher=_FailFetcher())
    br = mk.get_breadth()
    t.ok("涨跌家数回退缓存且标注 stale",
         br.get("stale") is True and br.get("rising") == 3000,
         f"stale={br.get('stale')} rising={br.get('rising')}")
    zt = mk.get_limit_up_stats()
    t.ok("涨停池回退缓存且标注 stale",
         zt.get("stale") is True and zt.get("limit_up_count") == 55,
         f"stale={zt.get('stale')} count={zt.get('limit_up_count')}")
    t.ok("兜底不含 error（避免被误报为数据缺口）", zt.get("error") is None,
         f"error={zt.get('error')}")

    # 板块排名兜底：实时失败回退磁盘缓存，且标注 sector_stale（选股据此告警）
    mk0._sector_disk_save([{"bk": "BK1", "name": "农业", "chg": 8.0, "rank": 1}])
    mk3 = Market(cfg, fetcher=_FailFetcher())
    sec = mk3.get_sector_ranking()
    t.ok("板块排名回退缓存且标注 sector_stale",
         mk3.sector_stale is True and len(sec) == 1 and sec[0]["name"] == "农业",
         f"stale={mk3.sector_stale} sec={[s['name'] for s in sec]}")
    t.ok("板块排名兜底条目带 stale 标记（共振分据此归零板块强度）",
         sec[0].get("stale") is True, f"sec[0].stale={sec[0].get('stale')}")
    ctx = mk3.sector_context({"industry": "农业", "code": "000001"})
    t.ok("sector_context 透传 stale（非种子路径的共振分同样归零）",
         ctx.get("stale") is True and ctx.get("found") is True and ctx.get("name") == "农业",
         f"stale={ctx.get('stale')} found={ctx.get('found')} name={ctx.get('name')}")


class _FakeZtPool:
    """模拟涨停池：只有 good_date 那天有数据，其余日期回空池。

    真接口实测：非交易日回 `rc=0, data={tc:0, pool:[]}`（取数成功、确实没有），
    而 ut 令牌不对回 `rc=205, data=null`（取数失败）。两者必须分开，所以这里严格
    按真接口的形状返回。
    """

    def __init__(self, good_date: str, n: int, max_boards: int, rc: int = 0):
        self.good_date = good_date
        self.n = n
        self.max_boards = max_boards
        self.rc = rc
        self.dates: list = []
        self.stats = {"requests": 0, "errors": 0, "cache_hits": 0}

    def get_json(self, url, params, host_pool=None):
        self.dates.append(str(params.get("date")))
        self.stats["requests"] += 1
        if self.rc != 0:
            return {"rc": self.rc, "data": None}
        if str(params.get("date")) != self.good_date:
            return {"rc": 0, "data": {"tc": 0, "pool": []}}
        pool = [{"lbc": (self.max_boards if i == 0 else 1)} for i in range(self.n)]
        return {"rc": 0, "data": {"tc": self.n, "pool": pool}}


def check_ztpool_fallback(t: Suite, cfg: Config) -> None:
    """涨停池：非交易日得回退到上一个交易日。

    涨停池是唯一按日期取的数据源。不回退就会出现「涨停 0 家」旁边坐着
    「前5板块均涨 10%」，情绪分和周期都是拿两个日期的数据拼出来的。
    """
    import datetime as _dt

    t.head("数据层 · 涨停池回退到交易日")

    real_now = utils.now
    try:
        sunday = _dt.datetime(2026, 8, 2, 16, 30)          # 周日
        utils.now = lambda when=None, _f=sunday: _f
        t.eq("周日的上一交易日是周五",
             utils.compact_date(utils.prev_trading_day(sunday.date())), "20260731")

        zt = _FakeZtPool("20260731", 68, 5)
        st = Market(cfg, fetcher=zt).get_limit_up_stats()
        t.eq("回退到上一交易日", st["date"], "20260731")
        t.eq("涨停家数", st["limit_up_count"], 68)
        t.eq("最高连板", st["max_boards"], 5)
        t.ok("标记了 fallback", st["fallback"] is True)
        t.ok("先试当日再往前找", zt.dates[0] == "20260802", f"实际顺序 {zt.dates}")

        # 显式指定日期时不能自作主张换日子：复盘、回填都靠它拿确定的那一天。
        zt2 = _FakeZtPool("20260731", 68, 5)
        st2 = Market(cfg, fetcher=zt2).get_limit_up_stats(date="20260803")
        t.eq("指定日期不回退", st2["date"], "20260803")
        t.eq("指定日期只请求一次", len(zt2.dates), 1)

        # ut 令牌失效（rc=205, data=null）不等于「今天没有涨停」。当成 0 的后果是：
        # 情绪公式拿 0 板扣 10 分，还可能触发冰点降仓把仓位砍到 1/4——一个取数
        # 失败悄悄变成减仓指令。这是真实踩过的坑：配置里的 ut 一直是错的。
        zt3 = _FakeZtPool("20260731", 68, 5, rc=205)
        st3 = Market(cfg, fetcher=zt3).get_limit_up_stats()
        t.ok("取数失败标记 ok=False", st3["ok"] is False)
        t.ok("取数失败不假装成 0 家", st3["limit_up_count"] is None,
             f"got={st3['limit_up_count']}")
        t.ok("取数失败时连板也是 None", st3["max_boards"] is None)
        t.ok("取数失败带错误描述", bool(st3.get("error")), f"error={st3.get('error')!r}")
        t.eq("坏令牌不往前翻日期", len(zt3.dates), 1)

        # 情绪公式遇到 None 得跳过这两项，而不是当成 0 去扣分 / 降仓。
        cls = sent_mod.compute_score({"index": {}, "sectors": [],
                                      "breadth": {"advance_ratio": 0.20},
                                      "limit_up": st3}, cfg)
        t.ok("取数失败不参与最高连板扣分",
             all(d["item"] != "最高连板" for d in cls["deltas"]),
             f"deltas={[d['item'] for d in cls['deltas']]}")
        t.ok("取数失败不触发冰点降仓",
             sent_mod.classify(cls, cfg)["ice_cut"] is False)
    finally:
        utils.now = real_now


class _FlakyTransport:
    """模拟按节点丢连接的东财：dead 集合里的 host 一律抛连接异常，其余回真数据。

    复刻真实现象：`push2` / `push2his` 某些节点 RemoteDisconnected，而 `push2delay`
    这类节点是通的。用它验证抓取器会绕开坏节点、并在会话里记住它们。
    """

    def __init__(self, cfg: Config, dead: set):
        self.f = Fetcher(cfg)
        self.f.delay_base = self.f.delay_spread = self.f.delay_after_error = 0.0  # 自测不等
        self.f.show_progress = False  # 自测故意造重试，不往报告里添提示行
        self.dead = dead
        self.hits: list = []

        def fake_get(full_url):
            host = full_url.split("//", 1)[1].split("/", 1)[0]
            self.hits.append(host)
            if host in self.dead:
                raise OSError("Remote end closed connection without response")
            return '{"ok": 1}'
        self.f._do_get = fake_get  # type: ignore[assignment]

    def get_json(self, url, params, host_pool=None):
        return self.f.get_json(url, params, host_pool=host_pool)


def check_host_failover(t: Suite, cfg: Config) -> None:
    """CDN 节点故障转移：坏节点不该拖垮取数。

    真实踩坑：修好代理后仍见 push2 / push2his RemoteDisconnected，而板块（同一
    节点池）却成功——旧实现每次随机挑节点，一次操作里多个请求各自随机，
    坏运气就整段失败。现在先活后死排序、重试走不同节点、会话内记住坏节点。
    """
    t.head("数据层 · CDN 节点故障转移")
    url = cfg.get("market.clist_url")
    pool = list(cfg.get("market.cdn_hosts_quote"))
    dead = {pool[0], pool[1]}  # 三选二坏，只剩最后一个能通
    good = pool[2]

    tr = _FlakyTransport(cfg, dead)
    tr.f.retries = len(pool)                       # 本例专测「轮不同节点」，把次数放开
    # 单次请求：哪怕连撞两个坏节点，重试也应落到好节点上
    js = tr.get_json(url, {"pn": 1}, host_pool="cdn_hosts_quote")
    t.ok("绕开坏节点最终取到数据", js.get("ok") == 1, f"hits={tr.hits}")
    t.ok("好节点没被误杀", good not in tr.f._dead_hosts)
    t.ok("试过的坏节点都进了黑名单",
         all(h in tr.f._dead_hosts for h in tr.hits if h in dead),
         f"hits={tr.hits} dead_hosts={tr.f._dead_hosts}")

    # 坏节点已知时，先活后死排序应让第一次尝试就直奔好节点
    tr.f._dead_hosts = set(dead)
    tr.hits.clear()
    js2 = tr.get_json(url, {"pn": 2}, host_pool="cdn_hosts_quote")
    t.ok("坏节点已知时首选好节点", tr.hits[0] == good, f"first={tr.hits[:1]}")
    t.ok("该请求仍成功", js2.get("ok") == 1)

    # 整池全坏：不能因为都在黑名单里就放弃，仍要逐次换不同节点试
    tr_all = _FlakyTransport(cfg, set(pool))
    tr_all.f.retries = len(pool)
    tr_all.f._dead_hosts = set(pool)
    try:
        tr_all.get_json(url, {"pn": 1}, host_pool="cdn_hosts_quote")
        t.ok("全坏时抛错", False)
    except Exception:
        t.ok("全坏时抛错", True)
    t.eq("retries 够时每个节点都试到（逐次换节点而非反复撞同一个）",
         len(set(tr_all.hits)), len(pool))

    # ---- 板块成分股：东财独家、无家可降，而种子扫描要扫 30 个板块×多页。
    # 东财整体不可用时每一环都死磕到顶，总耗时就从几十秒满到几分钟。
    fetcher = Fetcher(cfg)
    fetcher.delay_base = fetcher.delay_spread = fetcher.delay_after_error = 0.0
    fetcher.show_progress = False
    fetcher.retries = 8                       # 老配置可能把全局重试钉得很高
    tries: List[str] = []

    def always_down(full_url, **kw):
        tries.append(full_url)
        raise OSError("Remote end closed connection without response")

    fetcher._do_get = always_down  # type: ignore[assignment]
    mkt = Market(cfg, fetcher=fetcher)
    member_retries = max(1, int(cfg.get("market.member_retries", 2)))  # 节点池不再抬高它
    try:
        mkt.get_sector_members("BK0001")
        t.ok("成分股全失败时报错", False)
    except MarketError:
        t.ok("成分股全失败时报错", True)
    t.eq("成分股重试单独削到 member_retries", len(tries), member_retries)
    tries.clear()
    sector_retries = max(1, int(cfg.get("market.sector_retries", 3)))
    try:
        mkt.get_sector_ranking(force=True)
    except MarketError:
        pass
    t.eq("板块排名重试覆盖整条 quote 节点池（sector_retries）", len(tries), sector_retries)
    t.eq("sector_retries 作用域用完即还原为全局重试", fetcher.retries, 8)

    # 涨跌家数同样是东财独家无备源：第一个页面失败即整体失败，重试也应覆盖全节点池。
    tries.clear()
    breadth_retries = max(1, int(cfg.get("market.breadth_retries", 3)))
    try:
        mkt.get_breadth()
    except MarketError:
        pass
    t.eq("涨跌家数重试覆盖整条 quote 节点池（breadth_retries）", len(tries), breadth_retries)

    # 东财报价/K 线是主源（push2/push2his），同样走节点池；重试要覆盖各自整条池
    # （报价 3、K 线 4），别只试前两个节点就切到腾讯等备源。
    from .data.providers.eastmoney import EastmoneyProvider
    em = EastmoneyProvider(cfg, fetcher)
    tries.clear()
    try:
        em.fetch_klines("600519")
    except MarketError:
        pass
    kline_retries = max(1, int(cfg.get("market.kline_retries", 4)))
    t.eq("东财 K 线重试覆盖 kline 节点池（kline_retries）", len(tries), kline_retries)
    t.eq("kline_retries 作用域用完即还原", fetcher.retries, 8)
    tries.clear()
    try:
        em.fetch_quote("600519")
    except MarketError:
        pass
    quote_retries = max(1, int(cfg.get("market.quote_retries", 3)))
    t.eq("东财报价重试覆盖 quote 节点池（quote_retries）", len(tries), quote_retries)

    # ---- 重试次数不再被节点池大小抬高：retries=2 就只试 2 次，报错文案同步报 2。
    # 真实踩坑：旧实现取 max(retries, len(hosts))，kline 池 4 个节点把一家源的死磕
    # 拉到 4×8s，降级链要等半分钟才轮得到下家——广度该由链提供，不是节点池。
    f_cap = Fetcher(cfg)
    f_cap.delay_base = f_cap.delay_spread = f_cap.delay_after_error = 0.0
    f_cap.show_progress = False
    f_cap.retries = 2
    cap_hits: List[str] = []

    def cap_down(full_url, **kw):
        cap_hits.append(full_url)
        raise OSError("Remote end closed connection without response")

    f_cap._do_get = cap_down  # type: ignore[assignment]
    try:
        f_cap.get_json(cfg.get("market.kline_url"), {"secid": "1.600519"},
                       host_pool="cdn_hosts_kline")
        t.ok("retries=2 全失败时报错", False)
    except MarketError as exc:
        t.ok("retries=2 全失败时报错", True)
        t.ok("异常文案报真实尝试次数（非节点池大小）",
             "请求失败(2次):" in str(exc), str(exc))
    t.eq("retries=2 就只发 2 次请求（kline 池有 4 个节点）", len(cap_hits), 2)

    # ---- 首选节点记忆：同 pool_key 高频请求应优先复用上次成功的节点（少走弯路）
    tr_pref = _FlakyTransport(cfg, set())          # 全活：谁都能通
    tr_pref.get_json(url, {"pn": 1}, host_pool="cdn_hosts_quote")
    first_host = tr_pref.hits[-1]                   # 第 1 次成功落在哪个节点
    tr_pref.hits.clear()
    tr_pref.get_json(url, {"pn": 2}, host_pool="cdn_hosts_quote")
    second_first = tr_pref.hits[0]
    tr_pref.hits.clear()
    tr_pref.get_json(url, {"pn": 3}, host_pool="cdn_hosts_quote")
    third_first = tr_pref.hits[0]
    t.eq("首选记忆 · 第2次首访=第1次成功节点", second_first, first_host)
    t.eq("首选记忆 · 第3次首访=第1次成功节点", third_first, first_host)
    t.eq("首选节点已落入 _preferred_host",
         tr_pref.f._preferred_host.get("cdn_hosts_quote"), first_host)
    # 首选节点突然敲不开：失败路径应把它从偏好里清掉，不再顽固复用
    tr_pref.dead = {first_host}
    tr_pref.hits.clear()
    tr_pref.get_json(url, {"pn": 4}, host_pool="cdn_hosts_quote")
    t.ok("首选节点死亡后清掉偏好（改记新的成功节点）",
         tr_pref.f._preferred_host.get("cdn_hosts_quote") != first_host,
         f"pref={tr_pref.f._preferred_host}")

    # ---- requests 分支容错解码：生僻股名（GB2312 越界字节）不能整条 UnicodeDecodeError
    class _FakeResp:
        def __init__(self, content):
            self.content = content
            self.encoding = None

        def raise_for_status(self):
            pass

        @property
        def text(self):                            # 默认路径 strict：越界字节会崩
            return self.content.decode(self.encoding or "utf-8", "strict")

    class _FakeSession:
        trust_env = False

        def __init__(self, content):
            self._content = content

        def get(self, url_, headers=None, timeout=None, proxies=None):
            return _FakeResp(self._content)

    fdec = Fetcher(cfg)
    bad_bytes = "㵘财".encode("utf-8") + b"\xff"    # GB2312 里没有的序列 + 越界字节
    strict_crashed = False
    try:
        bad_bytes.decode("gb2312")                 # 默认 strict
    except UnicodeDecodeError:
        strict_crashed = True
    t.ok("前提成立：越界字节在 strict 下确会崩", strict_crashed)
    fdec._sess = _FakeSession(bad_bytes)
    dec = fdec._do_get("http://qt.gtimg.cn/q=sh600519", encoding="gb2312")
    t.ok("requests 分支容错解码不 crash 且保留 U+FFFD",
         isinstance(dec, str) and "\ufffd" in dec, repr(dec))

    # ---- 零请求的统计行是废话：stats_line 应返回空串让调用方跳过
    fz = Fetcher(cfg)
    t.eq("Fetcher 零请求统计行为空串", fz.stats_line(), "")
    fz.stats["requests"] = 3
    t.ok("有请求时统计行照常输出",
         fz.stats_line() != "" and "网络请求" in fz.stats_line(), fz.stats_line())

    # ---- 板块缓存 schema 版本化：结构不匹配（含旧格式无 __version__）删缓存重拉
    from .data.market import SECTOR_CACHE_SCHEMA_VERSION
    mkt_sc = Market(cfg)
    sc_path = cfg.data_file("sector_cache_file")
    utils.write_json(sc_path, {"ver": mkt_sc.SECTOR_CACHE_VER, "ts": 32503680000.0,
                               "date": "2026-08-03", "sectors": [{"bk": "BK0001"}]})
    t.ok("旧格式缓存（无 __version__）读取返回 None", mkt_sc._sector_disk_load() is None)
    t.ok("schema 不匹配触发缓存文件删除", not os.path.exists(sc_path))
    mkt_sc._sector_disk_save([{"bk": "BK0001", "name": "光伏", "chg": 1.0, "rank": 1}])
    loaded = mkt_sc._sector_disk_load()
    t.ok("v1 格式写入后正常读回 data",
         bool(loaded) and loaded[0]["bk"] == "BK0001", str(loaded))
    raw_sc = utils.read_json(sc_path)
    t.eq("落盘顶层含 __version__", raw_sc.get("__version__"), SECTOR_CACHE_SCHEMA_VERSION)
    t.ok("落盘 data 包裹原有缓存内容",
         isinstance(raw_sc.get("data"), dict) and "sectors" in raw_sc["data"], str(raw_sc))


# ============================================== 多数据源：五家的固定响应

# 五家源用贵州茅台 600519 同一天的行情做样本：价 1350.60、量 29614 手、
# 额 4.0158 亿、昨收 1362.00。各家原始单位各不相同（腾讯万元、新浪股+元、
# 网易元），换算对了才会在这个共同口径上对齐——所以除了逐字段断言，
# 还横向比一次四家报价的一致性。
_MT_PRICE, _MT_PRE = 1350.60, 1362.00
_MT_VOL_HANDS, _MT_AMOUNT_YI, _MT_CHG_PCT = 29614.0, 4.0158, -0.837

_EM_QUOTE_RAW = {
    "f43": 1350.60, "f44": 1358.00, "f45": 1345.00, "f46": 1355.00,
    "f47": 29614, "f48": 401580000.0, "f50": 0.91, "f58": "贵州茅台",
    "f100": "酿酒行业", "f116": 1696500000000.0, "f117": 1696000000000.0,
    "f168": 0.24, "f169": -11.40, "f170": -0.837,
}


def _tencent_quote_text(symbol: str) -> str:
    """qt.gtimg.cn 的 `~` 分隔行（GBK 纯文本，只能按下标取）。"""
    idx = symbol.endswith("000001") and symbol.startswith("sh")
    f = ["0"] * 55
    f[0] = "1"
    f[1], f[2] = ("上证指数", "000001") if idx else ("贵州茅台", "600519")
    if idx:
        f[3], f[4], f[32] = "3832.26", "3804.86", "0.72"
        return f'v_{symbol}="' + "~".join(f) + '";'
    f[3], f[4], f[5], f[6] = "1350.60", "1362.00", "1355.00", "29614"
    f[31], f[32], f[33], f[34] = "-11.40", "-0.84", "1358.00", "1345.00"
    f[37], f[38] = "40158", "0.24"          # 成交额万元 / 换手率
    f[44], f[45] = "16960.0", "16965.0"     # 流通市值 / 总市值（亿）
    f[46] = "9.87"                          # 市净率：拿错下标时它会冒充流通市值
    f[49] = "0.91"                          # 量比
    return f'v_{symbol}="' + "~".join(f) + '";'


def _sina_quote_text(symbol: str) -> str:
    """hq.sinajs.cn 的逗号分隔行：量是**股**、额是**元**，且不给涨跌幅。"""
    return (f'var hq_str_{symbol}="贵州茅台,1355.000,1362.000,1350.600,'
            f'1358.000,1345.000,1350.500,1350.600,2961400,401580000.000,'
            f'2026-08-03,15:00:00,00";')


#: 新浪 K 线回的是 JS 对象字面量（键不带引号），直接 json.loads 会报错。
_SINA_KLINE_JS = ('[{day:"2026-07-30",open:"1330.000",high:"1348.000",'
                  'low:"1325.000",close:"1340.000",volume:"2750000"},'
                  '{day:"2026-07-31",open:"1355.000",high:"1358.000",'
                  'low:"1345.000",close:"1350.600",volume:"2961400"}]')


def _netease_quote_text(nc: str) -> str:
    """JSONP 外壳 + 小数形式的 percent + 名为 turnover 实为成交额的字段。"""
    return ('_ntes_quote_callback({"%s":{"code":"%s","percent":-0.00837,'
            '"high":1358.0,"low":1345.0,"open":1355.0,"price":1350.6,'
            '"yestclose":1362.0,"updown":-11.4,"volume":2961400,'
            '"turnover":401580000,"name":"贵州茅台"}});' % (nc, nc))


#: 凤凰 record 行：[日期, 开, **高**, **收**, 低, 量]——全链路唯一的异类。
#: 按东财/腾讯的开/收/高/低 去读，收盘会变成 1358、最高会变成 1350.6，
#: 得出「最高 < 收盘」这种不可能的 K 线，而数值都在合理区间里、肉眼看不出来。
_IFENG_RECORD = [
    ["2026-07-30", "1330.00", "1348.00", "1340.00", "1325.00", "27500", "8.00", "0.60"],
    ["2026-07-31", "1355.00", "1358.00", "1350.60", "1345.00", "29614", "-11.40", "-0.84"],
]


class _MockSources:
    """五家源的固定响应（不联网）。fail 里的键 = 「这家这个接口挂了」。

    降级链的真正风险不在单家能不能解析，而在「前面几家挂了之后链还能不能走到底」，
    所以这个假抓取器按接口粒度注入故障，而不是整家一起开关。
    同时记下每次请求的编码与 Referer——新浪缺 Referer 会 403、腾讯不按 GBK 解会乱码，
    这两项在真网络下才暴露，离线只能验「有没有传对」。
    """

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.stats = {"requests": 0, "errors": 0, "cache_hits": 0}
        self.seen: List[tuple] = []      # (接口键, encoding, Referer)

    @staticmethod
    def _key(url: str) -> str:
        u = str(url)
        if "push2his" in u:
            return "em_kline"
        if "push2.eastmoney" in u or "push2delay" in u:
            return "em_quote"
        if "qt.gtimg" in u:
            return "tencent_quote"
        if "ifzq.gtimg" in u:
            return "tencent_kline"
        if "hq.sinajs" in u:
            return "sina_quote"
        if "money.finance.sina" in u:
            return "sina_kline"
        if "126.net" in u:
            return "netease_quote"
        if "finance.ifeng" in u:
            return "ifeng_kline"
        return "unknown"

    def _serve(self, url, encoding=None, extra_headers=None) -> str:
        key = self._key(url)
        self.stats["requests"] += 1
        self.seen.append((key, encoding, (extra_headers or {}).get("Referer")))
        if key in self.fail:
            self.stats["errors"] += 1
            raise MarketError(f"{key} 不可用（自测注入）")
        return key

    def get_text(self, url, params=None, host_pool=None, encoding=None,
                 extra_headers=None):
        key = self._serve(url, encoding, extra_headers)
        if key == "tencent_quote":
            return _tencent_quote_text(str(url).split("q=")[-1])
        if key == "sina_quote":
            return _sina_quote_text(str(url).split("list=")[-1])
        if key == "sina_kline":
            return _SINA_KLINE_JS
        if key == "netease_quote":
            return _netease_quote_text(str(url).rsplit("/", 1)[-1])
        raise MarketError(f"未定义的文本接口: {url}")

    def get_json(self, url, params, host_pool=None):
        key = self._serve(url)
        if key == "em_quote":
            if str(params.get("fields")) == "f43,f170":     # 指数点位专用字段
                return {"data": {"f43": 3832.26, "f170": 0.72}}
            return {"data": dict(_EM_QUOTE_RAW)}
        if key == "em_kline":
            if "000001" in str(params.get("secid")):
                return {"data": {"klines": [f"2026-07-{i + 1:02d},3790,3800,3810,3780,1,2"
                                            for i in range(20)]}}
            return {"data": {"klines": [
                "2026-07-30,1320.00,1340.00,1348.00,1325.00,27500,368000000",
                "2026-07-31,1355.00,1350.60,1358.00,1345.00,29614,401580000"]}}
        if key == "tencent_kline":
            symbol = str(url).split("param=")[-1].split(",")[0]
            if "000001" in symbol:
                rows = [[f"2026-07-{i + 1:02d}", "3790", "3800", "3810", "3780", "1"]
                        for i in range(20)]
            else:
                rows = [["2026-07-30", "1320.00", "1340.00", "1348.00", "1325.00", "27500"],
                        ["2026-07-31", "1355.00", "1350.60", "1358.00", "1345.00", "29614"]]
            return {"data": {symbol: {"qfqday": rows}}}
        if key == "ifeng_kline":
            return {"record": [list(r) for r in _IFENG_RECORD]}
        raise MarketError(f"未定义的 JSON 接口: {url}")


class _FlakyChainTransport:
    """真抓取器 + 按域名注入故障：alive 里的域名回真数据，其余一律抛连接异常。

    _MockSources 是把整个抓取器换掉的，验不到抓取层自己的提示；这里保留真 Fetcher，
    只把最底层的 _do_get 换掉，于是重试 / 退避 / 提示节流全是真的。
    """

    def __init__(self, cfg: Config, alive: tuple = ()):
        self.alive = alive
        self.f = Fetcher(cfg)
        self.f.delay_base = self.f.delay_spread = self.f.delay_after_error = 0.0
        self.f.retry_notice_gap = 0.0   # 关掉时间节流：逐次刷屏若还在，必然现形
        self.f.show_progress = True     # 真抓取器默认就是开的
        self.tries: List[str] = []
        self.f._do_get = self._get  # type: ignore[assignment]

    def _get(self, full_url, encoding=None, extra_headers=None):
        self.tries.append(full_url)
        if not any(k in full_url for k in self.alive):
            raise OSError("Remote end closed connection without response")
        return _tencent_quote_text(str(full_url).split("q=")[-1])


def check_providers(t: Suite, cfg: Config) -> None:
    """五源降级链：字段映射、单位换算、部分能力跳过与降级顺序。

    多接几家源的风险不是「取不到数」而是「取到错数」：各家单位不同（腾讯额记万元、
    新浪量记股、网易涨幅记小数）、凤凰 K 线还把高低与收盘排成开/高/收/低。
    这类错位不报错、数值也都在合理区间里，只会把市值/ATR/乖离静静算糊。
    所以四家报价源用同一天的茅台行情喂进去，要求换算后落到同一口径。
    """
    from .data.providers import build_provider, index_double_route
    from .data.providers.netease import netease_code

    t.head("数据层 · 五源降级链")
    ALL = ["eastmoney", "tencent", "sina", "netease", "ifeng"]

    # ---- 默认就该全开：只包东财一家的降级链等于没有降级
    t.eq("默认源列表全开五家",
         list(config_store.DEFAULTS["market"]["data_sources"]), ALL)
    solo = build_provider(cfg, _MockSources())
    t.eq("默认配置组出五家链", [p.name for p in solo.providers], ALL)
    t.eq("同一源重试次数已削到 2", config_store.DEFAULTS["market"]["retries"], 2)

    # ---- 四家报价源各自解析同一支票，换算后必须对齐
    quotes = {}
    for src in ("eastmoney", "tencent", "sina", "netease"):
        quotes[src] = build_provider(cfg, _MockSources(), [src]).fetch_quote("600519")

    q = quotes["tencent"]
    t.eq("腾讯报价 · 名称与现价", (q["name"], q["price"]), ("贵州茅台", _MT_PRICE))
    t.eq("腾讯报价 · 成交额万→亿", q["amount_yi"], _MT_AMOUNT_YI, tol=1e-6)
    t.eq("腾讯报价 · 总市值/流通市值不取反",
         (q["cap_yi"], q["float_cap_yi"]), (16965.0, 16960.0))
    t.eq("腾讯报价 · 量比/换手率", (q["vol_ratio"], q["turnover"]), (0.91, 0.24))
    t.ok("腾讯报价 · 市净率没冒充流通市值", q["float_cap_yi"] > 100,
         f"float_cap_yi={q['float_cap_yi']}")

    q = quotes["sina"]
    t.eq("新浪报价 · 成交量股→手", q["volume"], _MT_VOL_HANDS, tol=1e-6)
    t.eq("新浪报价 · 成交额元→亿", q["amount_yi"], _MT_AMOUNT_YI, tol=1e-6)
    t.eq("新浪报价 · 涨幅按昨收算出", q["chg_pct"], _MT_CHG_PCT, tol=1e-3)
    t.ok("新浪缺的字段宁缺勿造",
         q["vol_ratio"] is None and q["turnover"] is None and q["cap_yi"] is None)

    t.eq("网易代码前缀 · 沪 0 / 深 1",
         (netease_code("sh", "600519"), netease_code("sz", "000001")),
         ("0600519", "1000001"))
    q = quotes["netease"]
    t.eq("网易报价 · 成交量股→手", q["volume"], _MT_VOL_HANDS, tol=1e-6)
    t.eq("网易报价 · turnover 是额不是换手率",
         (q["amount_yi"], q["turnover"]), (_MT_AMOUNT_YI, None))
    t.eq("网易报价 · percent 小数×100", q["chg_pct"], _MT_CHG_PCT, tol=1e-6)

    t.ok("四家报价现价一致",
         all(abs(v["price"] - _MT_PRICE) < 1e-6 for v in quotes.values()),
         str({k: v["price"] for k, v in quotes.items()}))
    t.ok("四家报价成交量同单位（手）",
         all(abs(v["volume"] - _MT_VOL_HANDS) < 1e-6 for v in quotes.values()),
         str({k: v["volume"] for k, v in quotes.items()}))
    t.ok("四家报价成交额同单位（亿）",
         all(abs(v["amount_yi"] - _MT_AMOUNT_YI) < 1e-6 for v in quotes.values()),
         str({k: v["amount_yi"] for k, v in quotes.items()}))

    # ---- K 线：凤凰的开/高/收/低 异序是最容易错且最隐蔽的一处
    kl = build_provider(cfg, _MockSources(), ["ifeng"]).fetch_klines("600519", limit=30)
    last = kl[-1]
    t.eq("凤凰 K 线 · 开/高/收/低 映射正确",
         (last["open"], last["high"], last["close"], last["low"]),
         (1355.00, 1358.00, 1350.60, 1345.00))
    t.ok("凤凰 K 线 · 最高 ≥ 收盘 ≥ 最低（错位时必破）",
         last["high"] >= last["close"] >= last["low"], str(last))
    t.eq("凤凰 K 线 · 日期升序、新的在后", last["date"], "2026-07-31")

    kl = build_provider(cfg, _MockSources(), ["sina"]).fetch_klines("600519", limit=30)
    t.eq("新浪 K 线 · JS 裸键字面量能解", len(kl), 2)
    t.eq("新浪 K 线 · 收盘与成交量（股→手）",
         (kl[-1]["close"], kl[-1]["volume"]), (1350.60, _MT_VOL_HANDS))
    kl_tx = build_provider(cfg, _MockSources(), ["tencent"]).fetch_klines("600519", limit=30)
    t.eq("腾讯 K 线 · 与东财同序（开/收/高/低）",
         (kl_tx[-1]["open"], kl_tx[-1]["close"], kl_tx[-1]["high"], kl_tx[-1]["low"]),
         (1355.00, 1350.60, 1358.00, 1345.00))

    # ---- 编码与 Referer：传错了在真网络下是乱码与 403，离线只能验有没传
    mock = _MockSources()
    build_provider(cfg, mock, ["tencent"]).fetch_quote("600519")
    t.eq("腾讯请求声明 GBK", [e for k, e, _ in mock.seen if k == "tencent_quote"], ["gbk"])
    mock = _MockSources()
    build_provider(cfg, mock, ["sina"]).fetch_quote("600519")
    _, enc, ref = mock.seen[0]
    t.eq("新浪请求声明 GB2312", enc, "gb2312")
    t.eq("新浪请求带 Referer（缺了 403）", ref, "https://finance.sina.com.cn")

    # ---- 降级：东财+腾讯挂了，报价要能走到新浪
    mock = _MockSources(fail=("em_quote", "em_kline", "tencent_quote"))
    chain = build_provider(cfg, mock, ALL)
    got = chain.fetch_quote("600519")
    t.eq("报价降级到第三级（新浪）", chain.last_source.get("quote"), "sina")
    t.eq("降级后报价仍是正确的那支票", (got["code"], got["price"]), ("600519", _MT_PRICE))
    t.eq("供数源已标记进拓取器统计", mock.stats.get("source_used"), "sina")

    # ---- 降级到第四级：网易只有报价，凤凰没报价应被静默跳过
    mock = _MockSources(fail=("em_quote", "tencent_quote", "sina_quote"))
    chain = build_provider(cfg, mock, ALL)
    got = chain.fetch_quote("600519")
    t.eq("报价降级到第四级（网易）", chain.last_source.get("quote"), "netease")
    t.eq("网易供的数也是正确的那支票", got["code"], "600519")
    ifeng = [p for p in chain.providers if p.name == "ifeng"][0]
    t.eq("凤凰无报价能力→不计失败", (ifeng.stats["skipped"], ifeng.stats["failed"]), (0, 0))

    # ---- K 线降级：三家挂了轮到凤凰，而网易（无 K 线）静默跳过
    mock = _MockSources(fail=("em_kline", "tencent_kline", "sina_kline"))
    chain = build_provider(cfg, mock, ALL)
    kl = chain.fetch_klines("600519", limit=30)
    t.eq("K 线降级到第四级（凤凰）", chain.last_source.get("klines"), "ifeng")
    t.eq("凤凰供的 K 线收盘没错位", kl[-1]["close"], 1350.60, tol=1e-6)
    nete = [p for p in chain.providers if p.name == "netease"][0]
    t.eq("网易无 K 线能力→计入跳过、不计失败",
         (nete.stats["skipped"], nete.stats["failed"]), (1, 0))
    t.eq("三家真失败才计失败", chain.stats["failed"], 3)

    # ---- 全部挂掉：错误汇总只列真失败的源，不拿「没这个能力」凑数
    mock = _MockSources(fail=("em_quote", "tencent_quote", "sina_quote", "netease_quote"))
    chain = build_provider(cfg, mock, ALL)
    try:
        chain.fetch_quote("600519")
        msg = ""
        t.ok("全部挂掉时报错", False)
    except MarketError as exc:
        msg = str(exc)
        t.ok("全部挂掉时报错", True)
    t.ok("错误汇总列齐四家真失败",
         all(s in msg for s in ("eastmoney", "tencent", "sina", "netease")), msg)
    t.ok("不把「凤凰没报价」当成失败", "ifeng" not in msg, msg)

    # ---- 指数两路取源：报价挂了还能拿 K 线兑出点位
    def _boom():
        raise MarketError("指数报价挂了")

    snap = index_double_route(_boom, lambda: [{"close": 3800.0}] * 19 + [{"close": 3832.26}])
    t.eq("指数两路 · 报价挂了用收盘兑点位", snap["point"], 3832.26, tol=1e-6)
    t.ok("指数两路 · MA20 与点位同量级",
         abs(snap["point"] - snap["ma20"]) / snap["ma20"] < 0.5, str(snap))
    try:
        index_double_route(_boom, _boom)
        t.ok("指数两路 · 两路都挂才算真失败", False)
    except MarketError:
        t.ok("指数两路 · 两路都挂才算真失败", True)

    mock = _MockSources(fail=("em_quote",))
    chain = build_provider(cfg, mock, ALL)
    snap = chain.fetch_index_snapshot()
    t.eq("指数 · 内部兑得出数就不降级", chain.last_source.get("index_snapshot"), "eastmoney")
    t.eq("指数 · K 线兑出的点位", snap["point"], 3800.0, tol=1e-6)

    mock = _MockSources(fail=("em_quote", "em_kline"))
    chain = build_provider(cfg, mock, ALL)
    snap = chain.fetch_index_snapshot()
    t.eq("指数 · 东财两路全挂才降级到腾讯",
         chain.last_source.get("index_snapshot"), "tencent")
    t.eq("指数 · 腾讯点位不被缩放", snap["point"], 3832.26, tol=1e-6)
    t.eq("指数 · 腾讯 MA20", snap["ma20"], 3800.0, tol=1e-6)
    t.ok("指数 · 3832 > MA20 3800 → 在上方", snap["ma20_above"] is True)
    nete = [p for p in chain.providers if p.name == "netease"][0]
    t.eq("指数 · 无指数能力的源不计失败", nete.stats["failed"], 0)

    # ---- 每家每方法的超时：备源读自己的配置，不沿主源的 8s
    chain = build_provider(cfg, _MockSources(), ALL)
    by_name = {p.name: p for p in chain.providers}
    t.eq("腾讯报价超时读配置", by_name["tencent"].timeout_for("quote"), 4.0)
    t.eq("凤凰 K 线超时读配置", by_name["ifeng"].timeout_for("klines"), 5.0)
    t.eq("index_snapshot 映到 index 配置项",
         by_name["eastmoney"].timeout_for("index_snapshot"), 8.0)
    t.ok("没配置的方法回落全局超时",
         by_name["ifeng"].timeout_for("quote") == float(cfg.get("market.timeout")),
         f"got={by_name['ifeng'].timeout_for('quote')}")
    t.ok("降级摘要能说出谁供的数", "数据源" in chain.source_line(),
         chain.source_line())

    # ---- 命中统计：降级链到底有没接上，收尾要能一行说清
    mock = _MockSources(fail=("em_quote",))
    chain = build_provider(cfg, mock, ALL)
    chain.fetch_quote("600519")
    chain.fetch_klines("600519")
    t.eq("源命中计数分源统计", dict(chain.source_hit_count),
         {"tencent": 1, "eastmoney": 1})
    line = chain.provider_stats_line()
    t.ok("命中摘要报得出主备源各自供了多少",
         "东财 1" in line and "腾讯 1" in line, line)

    # ---- 切源提示：用户看到「改用腾讯」比看到「网络抖动」有信心
    mock = _MockSources(fail=("em_quote",))
    mock.show_progress = True                       # 真抓取器默认就是开的
    chain = build_provider(cfg, mock, ALL)
    buf = io_mod.StringIO()
    with contextlib.redirect_stdout(buf):
        chain.fetch_quote("600519")
    notice = buf.getvalue()
    t.ok("切源时报「谁挂了、改用谁」",
         "东财" in notice and "改用 腾讯" in notice, notice.strip() or "（无输出）")
    t.ok("切源提示不再叫「网络抖动」", "网络抖动" not in notice, notice)

    # 没能力的下家不能拿来充数：网易没 K 线，报「改用网易」是骗人
    mock = _MockSources(fail=("em_kline", "tencent_kline"))
    mock.show_progress = True
    chain = build_provider(cfg, mock, ALL)
    buf = io_mod.StringIO()
    with contextlib.redirect_stdout(buf):
        chain.fetch_klines("600519")
    notice = buf.getvalue()
    t.ok("下家只数真有这个能力的源",
         "网易" not in notice and "改用 新浪" in notice, notice)

    # 同一家源连挂多条记录（回填/扫描几十条 K 线）：切源提示只报第一次，不逐条刷
    mock = _MockSources(fail=("em_kline",))
    mock.show_progress = True
    chain = build_provider(cfg, mock, ALL)
    buf = io_mod.StringIO()
    with contextlib.redirect_stdout(buf):
        chain.fetch_klines("600519")
        chain.fetch_klines("600519")
    notice = buf.getvalue()
    t.eq("同一源连挂只报一次「改用谁」", notice.count("改用 腾讯"), 1)

    # ---- 会话熔断：东财 K 线整池挂掉后，后续 K 线直接问腾讯，不再每条白等重试
    mock = _MockSources(fail=("em_kline",))
    mock.show_progress = True
    chain = build_provider(cfg, mock, ALL)
    chain.fetch_klines("600519")          # 第一次：东财挂 → 腾讯接手
    em_kline_calls = sum(1 for k, _, _ in mock.seen if k == "em_kline")
    chain.fetch_klines("600519")          # 第二次：应跳过东财
    t.ok("熔断后不再重复打东财 K 线",
         sum(1 for k, _, _ in mock.seen if k == "em_kline") == em_kline_calls,
         f"seen={mock.seen}")
    t.eq("两次都由腾讯供数", chain.source_hit_count.get("tencent"), 2)

    # ---- 抓取层的「网络抖动」只在重试全部用尽时兜底一句：逐次重试都报是无信息
    # 重复（上层紧接着就说清了「谁挂了、改用谁」）。用户抄回来的日志里连刷 5 条
    # 「网络抖动，正在重试」就是这么来的，所以节流关掉也不得超过一条。
    tr = _FlakyChainTransport(cfg, alive=("qt.gtimg",))    # 东财全挂，腾讯接手
    chain = build_provider(cfg, tr.f, ALL)
    buf = io_mod.StringIO()
    with contextlib.redirect_stdout(buf):
        q = chain.fetch_quote("600519")
    notice = buf.getvalue()
    t.eq("抖动提示不逐次刷屏（整轮最多一条）", notice.count("网络抖动"), 1)
    t.ok("兜底提示带上域名与尝试次数",
         "eastmoney.com" in notice and "即将切换" in notice, notice.strip() or "（无输出）")
    t.ok("抖完仍由腾讯供上数",
         q["price"] == _MT_PRICE and chain.last_source.get("quote") == "tencent",
         str(chain.last_source))
    t.ok("兜底前确实经历了多次重试", len(tr.tries) > 1, f"tries={len(tr.tries)}")

    # 全部源都彻底挂掉时反过来不能全哑：它是单源 / 无可降级场景里唯一的用户反馈
    tr_dead = _FlakyChainTransport(cfg)
    chain = build_provider(cfg, tr_dead.f, ALL)
    buf = io_mod.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            chain.fetch_quote("600519")
            t.ok("全源全挂时报错", False)
        except MarketError:
            t.ok("全源全挂时报错", True)
    notice = buf.getvalue()
    t.ok("全源全挂仍至少兜底报一声", notice.count("网络抖动") >= 1,
         notice.strip() or "（无输出）")

    # ---- Market 门面确实走降级链（而不是自己直连东财）
    mock = _MockSources(fail=("em_quote", "em_kline"))
    mkt = Market(cfg, fetcher=mock, provider=build_provider(cfg, mock, ALL))
    mq = mkt.get_quote("600519")
    t.eq("Market.get_quote 走降级链", (mq["code"], mq["price"]), ("600519", _MT_PRICE))
    t.eq("Market.get_klines 走降级链", mkt.get_klines("600519")[-1]["close"],
         1350.60, tol=1e-6)
    t.eq("两项都由腾讯接手", mkt.provider.last_source.get("quote"), "tencent")

    # ---- 指数报价通、东财 K 线挂：get_index 跨源走 K 线降级链补 MA20
    mock = _MockSources(fail=("em_kline",))
    mkt = Market(cfg, fetcher=mock, provider=build_provider(cfg, mock, ALL))
    idx = mkt.get_index()
    t.eq("指数 · 报价通 K 线挂仍不整包降级",
         mkt.provider.last_source.get("index_snapshot"), "eastmoney")
    t.eq("指数 · 跨源 K 线补 MA20", idx["ma20"], 3800.0, tol=1e-6)
    t.ok("指数 · 3832 > MA20 3800 → 在上方", idx["ma20_above"] is True)
    t.eq("指数 · MA20 由腾讯 K 线补", mkt.provider.last_source.get("klines"), "tencent")

    # ---- _has_data 按 method 判有效性：半成品响应不能被当成命中
    from .data.providers.base import ChainedProvider, IDataProvider, _has_data
    t.ok("_has_data quote · 半成品无 price 判无数据",
         not _has_data({"code": "600519"}, "quote"))
    t.ok("_has_data quote · price>0 才算有数据",
         _has_data({"code": "600519", "price": _MT_PRICE}, "quote")
         and not _has_data({"code": "600519", "price": 0}, "quote"))
    t.ok("_has_data klines · 首元素 close>0 才算有",
         _has_data([{"close": 10.0}], "klines")
         and not _has_data([{"close": 0}], "klines") and not _has_data([], "klines"))
    t.ok("_has_data index · 需 point>0（指数的价位字段是 point）",
         _has_data({"point": 3200.0}, "index_snapshot")
         and not _has_data({"point": 0}, "index_snapshot"))

    class _HalfQuote(IDataProvider):
        name = "half"

        def fetch_quote(self, code: str) -> dict:
            return {"code": code}          # 半成品：非空 dict、无 error、却缺 price

    class _GoodQuote(IDataProvider):
        name = "good"

        def fetch_quote(self, code: str) -> dict:
            return {"code": code, "price": _MT_PRICE}

    chain = ChainedProvider([_HalfQuote(cfg, None), _GoodQuote(cfg, None)], cfg=cfg)
    got = chain.fetch_quote("600519")
    t.eq("半成品响应触发继续降级到下家", chain.last_source.get("quote"), "good")
    t.eq("降级后拿到有效价格", got.get("price"), _MT_PRICE)

    # ---- 网易 JSONP 空对象：fetch_quote 应 raise MarketError（而非 AttributeError），
    #      降级链应能捕获并 fallback 到下家
    from .data.providers.netease import NeteaseProvider

    class _EmptyNeteaseFetcher:
        def get_text(self, url, *a, **k):
            return "_ntes_quote_callback({});"     # 剥完外壳是空对象

    ne_empty = NeteaseProvider(cfg, _EmptyNeteaseFetcher())
    try:
        ne_empty.fetch_quote("600519")
        t.ok("网易空响应 raise MarketError", False)
    except MarketError:
        t.ok("网易空响应 raise MarketError", True)
    except AttributeError:
        t.ok("网易空响应 raise MarketError", False, "误抛 AttributeError")
    chain = ChainedProvider([NeteaseProvider(cfg, _EmptyNeteaseFetcher()),
                             _GoodQuote(cfg, None)], cfg=cfg)
    got = chain.fetch_quote("600519")
    t.eq("网易空响应后降级到下家", chain.last_source.get("quote"), "good")

    # ---- 零请求零命中的源摘要为空串（不打“网络请求 0 次”废话行）
    mock = _MockSources()                          # requests=0 / errors=0 / 无命中
    chain = build_provider(cfg, mock, ALL)
    t.eq("零请求零命中的源摘要为空串", chain.provider_stats_line(), "")


def check_sentiment(t: Suite, cfg: Config, mk: FakeMarket) -> dict:
    t.head("道 · 情绪评分（§4.2 表逐项复算）")
    t.eq("天气采集单路超时默认30s", float(cfg.get("sentiment.fetch_timeout_sec", 30)), 30.0)
    raw = {"index": mk.get_index(), "sectors": mk.get_sector_ranking(),
           "breadth": mk.get_breadth(), "limit_up": mk.get_limit_up_stats()}
    scored = sent_mod.compute_score(raw, cfg)

    # 按规格表独立重算：基准 50
    expect = 50.0
    expect += 8      # 涨跌比 66.7% ≥55%
    expect += 6      # 最高连板 5 ≥5
    expect += 12     # 上证在 MA20 上方
    expect += 8      # 上证 +0.85% ≥ +0.5%
    expect += 15 if len([s for s in SECTORS if s["chg"] >= 3.0]) >= 8 else 8   # 热点 5 个 ≥4
    avg5 = sum(s["chg"] for s in mk.get_sector_ranking()[:5]) / 5.0
    expect += -8 if avg5 >= 6 else (5 if 2 <= avg5 <= 5 else 0)
    t.eq("情绪总分", scored["score"], round(min(expect, 100.0), 1), tol=0.05)
    t.eq("涨跌比", round(scored["advance_ratio"], 4), round(3000 / 4500, 4), tol=1e-4)
    t.eq("前5板块均涨", round(scored["sector_summary"]["avg5"], 2), round(avg5, 2), tol=0.01)
    t.eq("热点板块数", scored["sector_summary"]["hot_n"],
         len([s for s in SECTORS if s["chg"] >= 3.0]))

    # 走真实门面（并行采集 + 评分 + 分类），拿到下游各模块实际消费的天气快照
    sent = sent_mod.get_sentiment(mk, cfg, force=True)
    t.eq("门面原始分 = 评分层输出", sent["raw_score"], scored["score"], tol=0.05)
    t.ok("周期/姿态", sent["cycle"] in (sent_mod.CYCLE_MAIN, sent_mod.CYCLE_CLIMAX)
         and sent["stance"] == sent_mod.STANCE_ATTACK,
         f"{sent['cycle']} / {sent['stance']} 乘数 ×{sent['base_pos_mult']}")
    t.ok("进攻姿态允许新开", bool(sent.get("allow_new")))
    t.ok("天气快照带大盘子结构（9分共振②要用）",
         (sent.get("index") or {}).get("chg_pct") is not None
         and "ma20_above" in (sent.get("index") or {}),
         f"index={sent.get('index')}")

    # §4.5 冰点降仓：最高连板 ≤3 且 涨跌比 <35% → 强制 ×0.25
    ice_raw = {"index": {"point": 3000.0, "chg_pct": -1.2, "ma20": 3150.0, "ma20_above": False},
               "sectors": [{"bk": "BKX", "name": "普跌", "chg": 0.4, "rank": 1,
                            "up_n": 1, "down_n": 50}],
               "breadth": {"advance_ratio": 0.30, "rising": 900, "falling": 2100},
               "limit_up": {"max_boards": 3, "limit_up_count": 6}}
    ice = sent_mod.classify(sent_mod.compute_score(ice_raw, cfg), cfg)
    t.eq("冰点降仓乘数", ice["base_pos_mult"], 0.25)
    t.eq("冰点周期", ice["cycle"], sent_mod.CYCLE_ICE)
    t.ok("冰点禁止新开", not ice.get("allow_new"), f"姿态 {ice['stance']}")

    # kline 单独挂：点位（quote）到手但 MA20（kline）取不到时，指数不该整块丢，
    # 且「不知道 MA20」不能被当成「在 MA20 下方」编出退潮（周期该照分走主升/发酵）。
    gap_raw = {"index": {"point": 3800.0, "chg_pct": -0.6, "ma20": None, "ma20_above": False},
               "sectors": [{"bk": f"BK{i}", "name": f"热{i}", "chg": 5.0, "rank": i,
                            "up_n": 30, "down_n": 2} for i in range(1, 8)],
               "breadth": {"advance_ratio": 0.74, "rising": 3800, "falling": 1300},
               "limit_up": {"max_boards": 6, "limit_up_count": 75}}
    gap_scored = sent_mod.compute_score(gap_raw, cfg)
    gap = sent_mod.classify(gap_scored, cfg)
    t.ok("kline 缺失时保留指数点位", (gap_scored.get("index") or {}).get("point") == 3800.0)
    t.ok("MA20 未知标为不知", gap["ma20_known"] is False)
    t.ok("MA20 未知不谎报退潮", gap["cycle"] != sent_mod.CYCLE_EBB, f"cycle={gap['cycle']}")
    t.ok("MA20 未知落防守", gap["stance"] == sent_mod.STANCE_DEFEND, f"stance={gap['stance']}")
    t.ok("MA20 未知仍允许新开", bool(gap.get("allow_new")))
    t.eq("防守+允许新开 文案", sent_mod.allow_new_label(
        {"allow_new": True, "stance": sent_mod.STANCE_DEFEND}),
        "未硬禁（防守缩手）")
    t.eq("进攻+允许新开 文案", sent_mod.allow_new_label(
        {"allow_new": True, "stance": sent_mod.STANCE_ATTACK}),
        "未硬禁")
    t.eq("禁止新开 文案", sent_mod.allow_new_label(
        {"allow_new": False, "stance": sent_mod.STANCE_EMPTY}),
        "禁止")

    # MA20 下方（已知）不再单独触发防守：位置已由共振「大盘趋势」维扣分表达，
    # 这里不再对同一信号罚第二遍（否则弱势市数学上无人能过）。
    below_raw = {"index": {"point": 3150.0, "chg_pct": 0.5, "ma20": 3200.0, "ma20_above": False},
                 "sectors": [{"bk": f"BK{i}", "name": f"热{i}", "chg": 5.0, "rank": i,
                              "up_n": 30, "down_n": 2} for i in range(1, 8)],
                 "breadth": {"advance_ratio": 0.55, "rising": 2500, "falling": 2000},
                 "limit_up": {"max_boards": 5, "limit_up_count": 60}}
    below = sent_mod.classify(sent_mod.compute_score(below_raw, cfg), cfg)
    t.eq("MA20 下方（已知）不再单独触发防守", below["stance"], sent_mod.STANCE_ATTACK,
         f"stance={below['stance']} notes={below.get('notes')}")

    # 数据缺口横幅：指数超时 + 网络失败 → 醒目汇总；无缺口 → 空串
    fetch_to = int(cfg.get("sentiment.fetch_timeout_sec", 30))
    s_gap = {"errors": [f"index: 超时 {fetch_to}s", "hard: 请求失败"], "limit_up_error": None}
    gaps = sent_mod.data_gap_summary(s_gap, "网络请求 27 次｜东财 1｜失败 9")
    t.ok("指数缺口映射为大盘指数", any(g.startswith("大盘指数: 超时") for g in gaps), str(gaps))
    t.ok("网络失败进缺口汇总", any("失败 9" in g for g in gaps), str(gaps))
    t.ok("缺口横幅醒目", "⚠️" in sent_mod.format_data_gap_banner(s_gap, "网络请求 27 次｜东财 1｜失败 9"))
    t.eq("无缺口返回空串", sent_mod.format_data_gap_banner({"errors": []}, ""), "")
    t.eq("仅 HTTP 失败、无数据缺口 → 不报警",
         sent_mod.format_data_gap_banner({"errors": []}, "网络请求 70 次｜失败 12"), "")
    return sent


def check_identity(t: Suite, cfg: Config, mk: FakeMarket) -> dict:
    t.head("术 · 身份判定（§6.1 / §6.4）")
    q = mk.get_quote(TARGET)
    sec = mk.sector_context(q)
    ind = mk.get_indicators(TARGET, q["price"])
    idn = ident_mod.judge(q, sec, ind, cfg)

    t.eq("板块内排名占比", round(sec["stock_rank_pct"], 3), round(3 / 30, 3), tol=1e-3)
    t.eq("板块涨停家数", sec["limit_up_count"], 2)

    # 独立复算：50 +20(内前20%) +15(板块≤10) +18(相对跟涨) +12(热板前排) +5(市值) +5(均线)
    rel = 5.20 / 6.50
    t.ok("相对跟涨系数达标", 0.42 <= rel <= 0.90, f"rel_ratio={rel:.2f}")
    raw_sum = 50 + 20 + 15 + 18 + 12 + 5 + 5
    t.eq("身份分（>100 应夹紧）", idn["score"], min(raw_sum, 100.0), tol=0.05)
    t.eq("身份层级", idn["tier"], ident_mod.TIER_LEADER)
    t.ok("热点板块判定", bool(idn["hot_sector"]), f"板块涨 {sec['chg']}% ≥6%")

    # 杂毛：后 50% -25、市值 -10、均线 -10、板块排名 +8（银行排名 7，>5 落入中档）
    q2 = mk.get_quote("600999")
    sec2 = mk.sector_context(q2)
    ind2 = mk.get_indicators("600999", q2["price"])
    idn2 = ident_mod.judge(q2, sec2, ind2, cfg)
    t.eq("杂毛身份分", idn2["score"], 50 - 25 + 8 - 10 - 10, tol=0.05)
    t.eq("杂毛层级", idn2["tier"], ident_mod.TIER_ZAMAO)
    t.ok("杂毛 flags ≥2", len(idn2["flags"]) >= 2, f"flags={idn2['flags']}")

    # §6.3 杂毛预警：门槛 6 → 7（龙头无加成时不破 6 的地板）
    th_leader = preflight.effective_threshold(idn, None, False, cfg)
    th_zamao = preflight.effective_threshold(idn2, None, False, cfg)
    t.eq("龙头门槛", th_leader["threshold"], int(cfg.s("pass_threshold", 6)))
    t.eq("杂毛门槛 +1", th_zamao["threshold"], th_leader["threshold"] + 1)
    return idn


def check_levels(t: Suite, cfg: Config, mk: FakeMarket) -> dict:
    t.head("术 · ATR 止损止盈 + 含滑点 R:R（§5.2 / §5.3）")
    q = mk.get_quote(TARGET)
    kl = mk.get_klines(TARGET)
    price = q["price"]

    # ATR(14) 独立复算：TR = max(H-L, |H-昨收|, |L-昨收|)
    trs = []
    for i in range(1, len(kl)):
        h, l, pc = kl[i]["high"], kl[i]["low"], kl[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    want_atr = sum(trs[-14:]) / 14.0
    got_atr = indicators.atr(kl, 14)
    t.eq("ATR(14)", round(got_atr, 6), round(want_atr, 6), tol=1e-6)

    ind = mk.get_indicators(TARGET, price)
    want_atr_pct = want_atr / price * 100.0
    t.eq("ATR%", round(ind["atr_pct"], 6), round(want_atr_pct, 6), tol=1e-6)
    t.ok("均线多头 MA5≥MA10≥MA20", bool(ind["ma_bull"]),
         f"{utils.num(ind['ma5'])}/{utils.num(ind['ma10'])}/{utils.num(ind['ma20'])}")
    t.ok("日K最后一根为今日最近数据（kline_stale=False）",
         ind.get("kline_stale") is False, f"kline_date={ind.get('kline_date')}")

    # 日K最后一根落在旧日期 → 技术指标标记 stale，供共振评分弃用
    old = indicators.compute_indicators([{**k, "date": "2020-01-02"} for k in kl], price)
    t.ok("日K最后一根为旧日期时 kline_stale=True",
         old.get("kline_stale") is True, f"kline_date={old.get('kline_date')}")

    lv = preflight.compute_levels(price, ind["atr_pct"], cfg)
    want_sl = min(utils.clamp(want_atr_pct * 1.5, 2.0, 8.0), 6.0)
    t.eq("止损% = ATR%×1.5（夹 2~8，硬顶 6）", lv["sl_pct"], round(want_sl, 2), tol=0.011)
    t.ok("止盈% ≤ 15% 上限", lv["tp_pct"] <= 15.0 + 1e-9, f"tp={lv['tp_pct']}")

    # 含滑点 odds 独立复算
    adj_entry = price * 1.005
    adj_stop = price * (1 - lv["sl_pct"] / 100.0) * 0.995
    target = price * (1 + lv["tp_pct"] / 100.0)
    want_odds = (target - adj_entry) / (adj_entry - adj_stop)
    t.eq("R:R（买入+0.5% / 止损-0.5%）", lv["odds"], round(want_odds, 2), tol=0.02)
    # min_odds 已从 3 降到 2（止损硬顶 6% + 止盈 15% 结构下 3 恒不可达），
    # 断言改为「达到当前 min_odds 且 odds_ok」。
    min_odds = float(cfg.s("min_odds", 2))
    t.ok(f"R:R ≥ {min_odds:.0f}（min_odds）且 odds_ok",
         lv["odds"] >= min_odds - 1e-9 and lv["odds_ok"], f"odds={lv['odds']}")
    t.eq("打平胜率 = 1/(1+odds)", lv["breakeven_wr"],
         round(1.0 / (1.0 + lv["odds"]), 4), tol=1e-3)
    t.ok("止损/ATR ≤ 2.5（止损结构分前提）", lv["sl_atr_mult"] <= 2.5,
         f"sl/ATR={lv['sl_atr_mult']}")

    # 反推：min_tp_for_odds 应恰好把 odds 顶到 3
    need = preflight.min_tp_for_odds(price, lv["sl_pct"], 3.0, cfg)
    chk = preflight.odds_calc(price, lv["sl_pct"], need, cfg)
    t.eq("反推最低止盈的 odds", chk["odds"], 3.0, tol=0.02)
    return lv


def check_scoring(t: Suite, cfg: Config, mk: FakeMarket, sent: dict, lv: dict) -> dict:
    t.head("术 · 9 分共振（§5.1 六维逐项）")
    q = mk.get_quote(TARGET)
    sec = mk.sector_context(q)
    ind = mk.get_indicators(TARGET, q["price"])
    sc = preflight.score_nine(q, ind, sec, sent, lv, has_news=True, cfg=cfg)
    by_no = {d["no"]: d for d in sc["dims"]}

    t.eq("①板块强度（排名1≤3 且涨停2≥2，内前10% +1 封顶2）", by_no[1]["score"], 2)
    t.eq("②大盘趋势（强趋势：上方 + 上行）", by_no[2]["score"], 1)
    t.eq("③消息面（有催化）", by_no[3]["score"], 1)
    t.eq("④市值区间（120亿 ∈ 50~300）", by_no[4]["score"], 2)
    t.eq("⑤量价结构（放量上涨 + 多头，无扣分）", by_no[5]["score"], 2)
    t.eq("⑥止损结构（≤8% 且 /ATR≤2.5 且 R:R≥3）", by_no[6]["score"], 1)
    t.eq("共振总分", sc["total"], 9)
    t.eq("满分基准", sc["max"], 9)
    t.ok("无量价扣分项", not sc["penalties"], f"penalties={sc['penalties']}")

    # 消息面中性化：自动扫描无消息数据（has_news=None）→ 该维 0/0、不计分不占满分
    sc_neutral = preflight.score_nine(q, ind, sec, sent, lv, has_news=None, cfg=cfg)
    by_no_n = {d["no"]: d for d in sc_neutral["dims"]}
    t.eq("无消息数据时③消息面中性化为 0/0",
         (by_no_n[3]["score"], by_no_n[3]["max"]), (0, 0))
    t.eq("中性化后满分 9→8", sc_neutral["max"], 8)
    t.eq("中性化后总分=其余 5 维之和", sc_neutral["total"], 8)
    t.ok("中性化明细带标识",
         "中性" in by_no_n[3]["detail"] and "不计分" in by_no_n[3]["detail"],
         by_no_n[3]["detail"])

    # 扣分项：换手 >20% 应扣 1 分
    q_hi = dict(q, turnover=22.0)
    sc2 = preflight.score_nine(q_hi, ind, sec, sent, lv, has_news=True, cfg=cfg)
    t.eq("换手 22% 触发量价扣分", sc2["total"], 8)

    # 缩量上涨且非多头 → 0分（后继乏力：今日硬拉但没后劲，不再给「量价基本配合」1分）
    q_weak = dict(q, vol_ratio=1.1)
    ind_weak = dict(ind, ma_bull=False)
    sc_weak = preflight.score_nine(q_weak, ind_weak, sec, sent, lv, has_news=True, cfg=cfg)
    by5_weak = {d["no"]: d for d in sc_weak["dims"]}[5]
    t.eq("缩量上涨且非多头 → 量价 0 分", by5_weak["score"], 0)

    # 放量下跌 → 量价 -1 分（放量=大资金出逃，是强负信号，比 0 分更重）
    q_drop = dict(q, chg_pct=-2.0, vol_ratio=1.5)
    sc_drop = preflight.score_nine(q_drop, ind, sec, sent, lv, has_news=True, cfg=cfg)
    by5_drop = {d["no"]: d for d in sc_drop["dims"]}[5]
    t.eq("放量下跌 → 量价 -1 分", by5_drop["score"], -1)

    # 「乖离>8%」量价扣分已移除：乖离交由 veto 统一把关（普通 15% / 龙头 25%），
    # 不再在量价结构里用更严的 8% 罚强势票（高乖离反而赢）。
    ind_hi_bias = dict(ind, bias_ma20=24.0)
    sc_hibias = preflight.score_nine(q, ind_hi_bias, sec, sent, lv, has_news=True, cfg=cfg)
    t.ok("乖离 24% 不再触发量价扣分",
         not any("乖离" in p for p in sc_hibias["penalties"]),
         f"penalties={sc_hibias['penalties']}")

    # 板块排名磁盘兜底（stale）：板块强度整维归零，不拿昨日排名给假分。
    sec_stale = dict(sec, stale=True)
    sc_stale = preflight.score_nine(q, ind, sec_stale, sent, lv, has_news=True, cfg=cfg)
    by_no_stale = {d["no"]: d for d in sc_stale["dims"]}
    t.eq("stale 时①板块强度归零", by_no_stale[1]["score"], 0)
    t.eq("stale 时共振总分扣掉板块强度（9→7）", sc_stale["total"], 7)
    t.ok("stale 提示出现在板块强度明细里",
         "不参与共振分" in by_no_stale[1]["detail"], by_no_stale[1]["detail"])

    # 技术指标 stale（日K最后一根非今日）：量价结构、止损结构两维弃用该指标。
    ind_stale = dict(ind, kline_stale=True)
    sc_kline_stale = preflight.score_nine(q, ind_stale, sec, sent, lv, has_news=True, cfg=cfg)
    by_kline_stale = {d["no"]: d for d in sc_kline_stale["dims"]}
    t.eq("K线 stale 时⑤量价结构归零", by_kline_stale[5]["score"], 0)
    t.eq("K线 stale 时⑥止损结构归零", by_kline_stale[6]["score"], 0)
    t.eq("K线 stale 时共振总分（9→6）", sc_kline_stale["total"], 6)
    t.ok("K线 stale 提示出现在⑤量价结构明细里",
         "不参与共振分" in by_kline_stale[5]["detail"], by_kline_stale[5]["detail"])
    t.ok("K线 stale 提示出现在⑥止损结构明细里",
         "不参与共振分" in by_kline_stale[6]["detail"], by_kline_stale[6]["detail"])
    return sc


def check_winrate_score(t: Suite, cfg: Config) -> None:
    """胜率选股评分：按实证胜率因素加权（数据启发，与 9 分共振并行）。"""
    t.head("术 · 胜率选股评分（数据型）")
    strong = {
        "sector": {"rank": 1}, "stage": {"stage": "过热"},
        "identity": {"score": 90.0},
        "quote": {"chg_pct": 4.0, "vol_ratio": 1.5},
        "ind": {"ma_bull": True},
    }
    wr = preflight.winrate_score(strong, cfg)
    t.eq("胜率评分（强票）=7", wr["score"], 7)  # 3+1+1+1+1

    weak = {
        "sector": {"rank": 9}, "stage": {"stage": "突破"},
        "identity": {"score": 60.0},
        "quote": {"chg_pct": -2.0, "vol_ratio": 1.5},
        "ind": {"ma_bull": False},
    }
    wr2 = preflight.winrate_score(weak, cfg)
    t.eq("胜率评分（弱票）=-5", wr2["score"], -5)  # -2-1-1-1


def check_veto(t: Suite, cfg: Config, mk: FakeMarket, idn: dict) -> None:
    t.head("术 · VETO 一票否决（§7 阈值边界）")
    base = mk.get_quote(TARGET)
    ind = mk.get_indicators(TARGET, base["price"])

    def names(res: dict, kind: str) -> List[str]:
        return [i["name"] for i in res.get(kind, [])]

    clean = veto_mod.check(base, ind, idn, 0.60, cfg)
    t.ok("正常标的无否决", not clean["has_veto"], f"items={names(clean, 'items')}")

    r = veto_mod.check(dict(base, chg_pct=9.6), ind, idn, 0.60, cfg)
    t.ok("涨幅 9.6% ≥9.5% → 硬否决", "limit_up" in names(r, "hard"))
    r = veto_mod.check(dict(base, chg_pct=9.2), ind, idn, 0.60, cfg)
    t.ok("涨幅 9.2% ≥9.0% → 软否决（等回踩）", "near_limit_up" in names(r, "soft"))
    r = veto_mod.check(dict(base, turnover=25.5), ind, idn, 0.60, cfg)
    t.ok("换手 25.5% >25% → 硬否决", "turnover" in names(r, "hard"))
    r = veto_mod.check(dict(base, name="*ST测试", is_st=True), ind, idn, 0.60, cfg)
    t.ok("ST → 硬否决", "st" in names(r, "hard"))
    r = veto_mod.check(base, dict(ind, bias_ma20=31.0), idn, 0.60, cfg)
    t.ok("乖离 31% >30% → 硬否决", "bias_hard" in names(r, "hard"))
    r = veto_mod.check(base, dict(ind, bias_ma20=26.0), idn, 0.60, cfg)
    t.ok("龙头乖离 26% >25% → 软否决", "bias_ma20" in names(r, "soft"))
    r = veto_mod.check(base, dict(ind, bias_ma20=16.0),
                       {"tier": ident_mod.TIER_FOLLOW, "score": 50}, 0.60, cfg)
    t.ok("普通乖离 16% >15% → 软否决", "bias_ma20" in names(r, "soft"))
    r = veto_mod.check(base, ind, idn, 0.96, cfg)
    t.ok("分时 96% ≥95% → 硬否决", "intraday_hard" in names(r, "hard"))
    # 强势豁免：TARGET 是放量上涨+多头，改涨幅为负使其变「弱势」，才应被分时高位否决
    r = veto_mod.check(dict(base, chg_pct=-1.0), ind,
                       {"tier": ident_mod.TIER_FOLLOW, "score": 50}, 0.80, cfg)
    t.ok("弱势跟风分时 80% >75% → 软否决", "intraday_high" in names(r, "soft"))
    r = veto_mod.check(base, ind, idn, 0.80, cfg)
    t.ok("龙头分时 80% ≤85% → 放行", not r["has_veto"])
    r = veto_mod.check(base, ind, {"tier": ident_mod.TIER_FOLLOW, "score": 50}, 0.90, cfg)
    t.ok("强势票分时 90% 豁免（放量+多头）", not r["has_veto"] and r.get("strong_exempt"))
    old_exempt = cfg.get("veto.intraday_strong_exempt")
    cfg.set("veto.intraday_strong_exempt", False)
    try:
        r = veto_mod.check(base, ind, {"tier": ident_mod.TIER_FOLLOW, "score": 50}, 0.90, cfg)
        t.ok("关闭强势豁免后分时 90% → 软否决", "intraday_high" in names(r, "soft"))
    finally:
        cfg.set("veto.intraday_strong_exempt", old_exempt)

    # 20cm 板阈值等比放大：创业板涨停区 = 9.5×2 = 19%
    gem = _quote("300123", "创业测试", 30.0, 15.0, HOT_NAME)
    r = veto_mod.check(gem, ind, idn, 0.60, cfg)
    t.ok("创业板涨 15% 未入涨停区（阈值×2）", "limit_up" not in names(r, "hard"))
    r = veto_mod.check(dict(gem, chg_pct=19.5), ind, idn, 0.60, cfg)
    t.ok("创业板涨 19.5% ≥19% → 硬否决", "limit_up" in names(r, "hard"))

    # 涨停幅度按板区分：北交所 30%（原 bug 漏掉 bse 返回 10%，把 15% 涨幅误判涨停）
    t.eq("北交所涨停幅度 30%", utils.limit_up_pct("920176", "维琪科技"), 30.0)
    t.eq("北交所(8开头)涨停幅度 30%", utils.limit_up_pct("830001", "北交测试"), 30.0)
    t.eq("创业板涨停幅度 20%", utils.limit_up_pct("300123", "创业测试"), 20.0)
    t.eq("科创板涨停幅度 20%", utils.limit_up_pct("688001", "科创测试"), 20.0)
    t.eq("主板涨停幅度 10%", utils.limit_up_pct("600123", "主板测试"), 10.0)
    t.eq("ST 涨停幅度 5%", utils.limit_up_pct("600123", "ST测试"), 5.0)

    cfg.set("permissions.gem", False)
    r = veto_mod.check(gem, ind, idn, 0.60, cfg)
    t.ok("关闭创业板权限 → 硬否决", "board_permission" in names(r, "hard"))
    cfg.set("permissions.gem", True)

    t.ok("分时位置公式", abs(indicators.intraday_position(12.0, 12.1, 11.5)
                          - (12.0 - 11.5) / (12.1 - 11.5)) < 1e-9)


def check_position(t: Suite, cfg: Config) -> None:
    t.head("法 · 仓位与期望值（§10 / §11）")
    capital, price = 100000.0, 12.00
    s = portfolio.compute_position(capital, price, 1.0, cfg, sl_pct=3.0)
    lot = s["lot"]
    half = capital * float(cfg.s("max_position_pct", 0.50)) * 1.0
    t.eq("半仓额度 = 资金 × 50% × 乘数", s["half_pos"], round(half, 2), tol=0.01)
    t.ok("总股数向下取整到 1 手",
         s["full_shares"] * price <= half + 1e-6 and (s["full_shares"] + lot) * price > half,
         f"full={s['full_shares']} 手={lot}")
    t.eq("灰度 + 确认 = 总股数", s["gray_shares"] + s["confirm_shares"], s["full_shares"])
    t.ok("灰度约占 30%", abs(s["gray_shares"] / s["full_shares"] - 0.30) < 0.05,
         f"gray={s['gray_shares']}/{s['full_shares']}")
    t.eq("风险敞口 = 总金额 × 止损%", s["risk_amount"],
         round(s["full_amount"] * 3.0 / 100.0, 2), tol=0.02)

    s_ice = portfolio.compute_position(capital, price, 0.25, cfg, sl_pct=3.0)
    t.ok("冰点乘数 0.25 → 仓位约为 1/4",
         abs(s_ice["full_amount"] / s["full_amount"] - 0.25) < 0.03,
         f"{s_ice['full_amount']} vs {s['full_amount']}")
    s_clamp = portfolio.compute_position(capital, price, 5.0, cfg, sl_pct=3.0)
    t.eq("乘数上夹紧至 1.0", s_clamp["half_pos_mult"], 1.0)
    s_clamp2 = portfolio.compute_position(capital, price, 0.01, cfg, sl_pct=3.0)
    t.eq("乘数下夹紧至 0.25", s_clamp2["half_pos_mult"], 0.25)

    # 期望值 E[R] = p̂ × odds − (1 − p̂)
    e = exp_mod.evaluate(9, 3.0, cfg)
    p = float(e["p_hat"])
    t.eq("E[R] 公式", e["er"], round(p * 3.0 - (1 - p), 4), tol=1e-3)
    t.eq("打平胜率", e["breakeven_wr"], round(1.0 / (1.0 + 3.0), 4), tol=1e-3)
    t.ok("无历史样本时按默认胜率且标记样本不足",
         bool(e.get("insufficient")), f"p̂={p}")
    e_neg = exp_mod.evaluate(6, 0.5, cfg)
    t.ok("低赔率 → 非正期望", not e_neg["positive"], f"E[R]={e_neg['er']}")


def check_manual_position(t: Suite, cfg: Config, mk: FakeMarket) -> None:
    """手动录入持仓 + 盈亏与种子对照。"""
    from .phases import IO
    from .runtime import cli, runner

    t.head("持仓 · 手动录入与盈亏对照")
    t.eq("成本价空格容错 1 .109 → 1.109", cli._clean_number("1   .109"), "1.109")
    t.eq("全角空格容错", cli._clean_number("1\u3000.109"), "1.109")
    t.eq("全角数字容错", cli._clean_number("１２３.４"), "123.4")
    portfolio.remove_position(TARGET, cfg)
    res = portfolio.add_manual_position(TARGET, TARGET_NAME, 1000, 10.0, cfg,
                                        sl_pct=6.0, tp_pct=15.0)
    t.ok("手动录入成功", res.get("pos") is not None)
    t.eq("新增标记 updated=False", res.get("updated"), False)
    pos = res["pos"]
    t.eq("股数按输入", pos["shares"], 1000)
    t.eq("阶段为满仓", pos["stage"], portfolio.STAGE_FULL)
    t.eq("来源标 manual", pos.get("source"), "manual")
    t.eq("止损价按 -6% 计算", pos["stop"], round(10.0 * 0.94, 4))

    io = IO(interactive=False, quiet=True)
    r = runner.holdings_review(cfg=cfg, market=mk, io=io)
    t.eq("手动持仓浮动盈亏（现价12-成本10）×1000", r["total_pnl"], 2000.0, tol=0.01)
    t.eq("持仓成本合计", r["total_cost"], 10000.0, tol=0.01)
    t.ok("持仓出现在结果里", any(x["code"] == TARGET for x in r["positions"]))

    # 幂等：同代码重复录入 → 覆盖更新，不新增
    res2 = portfolio.add_manual_position(TARGET, TARGET_NAME, 500, 11.0, cfg)
    t.eq("重复录入覆盖更新 updated=True", res2.get("updated"), True)
    t.eq("覆盖后股数", res2["pos"]["shares"], 500)
    t.eq("覆盖后成本价", res2["pos"]["entry"], 11.0)
    t.eq("持仓数仍为 1（代码维度幂等）", len(portfolio.positions(cfg)), 1)

    portfolio.remove_position(TARGET, cfg)
    t.eq("删除后持仓清空", portfolio.positions(cfg), [])


def check_colors(t: Suite) -> None:
    """统一颜色方案：sign_color 语义 + COLOR_SEED 与盈亏/警告色互斥。"""
    t.head("颜色 · 统一方案")
    t.eq("sign_color 正 → 盈利绿", utils.sign_color(5.0), utils.COLOR_PROFIT)
    t.eq("sign_color 负 → 亏损红", utils.sign_color(-5.0), utils.COLOR_LOSS)
    t.eq("sign_color 零 → 中性白", utils.sign_color(0.0), utils.COLOR_NEUTRAL)
    t.eq("sign_color None → 警告黄", utils.sign_color(None), utils.COLOR_WARN)

    others = {utils.COLOR_PROFIT, utils.COLOR_LOSS, utils.COLOR_WARN,
              utils.COLOR_INFO, utils.COLOR_NEUTRAL}
    t.ok("COLOR_SEED 与盈亏/警告/信息/中性均不同",
         utils.COLOR_SEED not in others, f"seed={utils.COLOR_SEED} others={others}")
    t.ok("语义色均能生成 ANSI 高亮",
         all(utils.hl("x", c).startswith("\033[") for c in
             (utils.COLOR_PROFIT, utils.COLOR_LOSS, utils.COLOR_WARN,
              utils.COLOR_SEED, utils.COLOR_INFO, utils.COLOR_NEUTRAL)))


def check_gates(t: Suite, cfg: Config, mk: FakeMarket, sent: dict) -> None:
    t.head("法 · 门禁（§8.1 / §8.2 / §8.3）")
    gates.reset_state(cfg)
    plan_mod.clear_plan(cfg)

    # 无计划 → 代码门禁拦截（核心纪律）
    g = gates.check_code_gate(TARGET, sent, cfg)
    t.ok("无计划 → 禁止评估", not g.allowed,
         "；".join(b["rule"] for b in g.blocks))
    t.ok("拦截理由含计划绑定", any(b["rule"] == "计划绑定" for b in g.blocks))

    # 空仓姿态 → 会话门禁拦截
    empty = dict(sent, stance=sent_mod.STANCE_EMPTY, allow_new=False)
    g = gates.check_session_start(empty, cfg, force=False, require_window=False)
    t.ok("空仓姿态 → 禁止开会话", not g.allowed,
         "；".join(b["rule"] for b in g.blocks))

    # MA20 下方 → 不再硬拦（改由共振「大盘趋势」维分级扣分表达）
    below = dict(sent, ma20_above=False)
    g = gates.check_session_start(below, cfg, force=False, require_window=False)
    t.ok("上证 MA20 下方 → 不再硬拦", not any(b["rule"] == "上证MA20" for b in g.blocks),
         "；".join(b["rule"] for b in g.blocks))

    # 单日评估上限
    gates.reset_state(cfg)
    for _ in range(int(cfg.s("daily_max_evaluations", 5))):
        gates.bump_evaluation("600000", cfg)
    g = gates.check_session_start(sent, cfg, force=False, require_window=False)
    t.ok("单日评估达上限 → 禁止", any(b["rule"] == "单日评估" for b in g.blocks))

    # 同票复筛上限
    gates.reset_state(cfg)
    for _ in range(int(cfg.s("daily_max_symbol_evaluations", 2))):
        gates.bump_evaluation(TARGET, cfg)
    g = gates.check_code_gate(TARGET, sent, cfg)
    t.ok("同票复筛达上限 → 禁止", any(b["rule"] == "同票复筛" for b in g.blocks))

    # 单日新开上限
    gates.reset_state(cfg)
    gates.bump_new_open(cfg)
    g = gates.check_buy_gate(TARGET, None, sent, cfg)
    t.ok("单日新开达上限 → 禁止买入", any(b["rule"] == "单日新开" for b in g.blocks))
    gates.reset_state(cfg)


def check_market_trend_deduction(t: Suite, cfg: Config, mk: FakeMarket) -> None:
    """大盘趋势不再硬闸短路，改为 9 分共振里的分级扣分（-1~+1，扣分封顶 -1）。

    更稳的趋势定义：位置（点位 vs MA20，带缓冲）+ 方向（MA20 斜率）合成 -2~+2，
    弱势市共振分被扣除、但扫描照常跑完，不再一票否决。
    """
    t.head("扫描 · 大盘趋势分级扣分")
    q = mk.get_quote(TARGET)
    sec = mk.sector_context(q)
    ind = mk.get_indicators(TARGET, q["price"])
    lv = {"sl_pct": 6.0, "odds": 2.08, "sl_atr_mult": 0.8}

    def dim2(index: dict) -> int:
        sc = preflight.score_nine(q, ind, sec, {"index": index}, lv,
                                  has_news=True, cfg=cfg)
        return {d["no"]: d for d in sc["dims"]}[2]["score"]

    t.eq("强趋势 +1（上方且上行）", dim2({"point": 3200.0, "ma20": 3150.0,
                                          "ma20_bias_pct": 1.59, "ma20_slope_pct": 0.5}), 1)
    t.eq("弱势 -1（下方且下行，扣分封顶）", dim2({"point": 3150.0, "ma20": 3200.0,
                                        "ma20_bias_pct": -1.56, "ma20_slope_pct": -0.5}), -1)
    t.eq("贴线震荡 0（缓冲内且走平）", dim2({"point": 3200.0, "ma20": 3198.0,
                                             "ma20_bias_pct": 0.06, "ma20_slope_pct": 0.05}), 0)
    t.eq("偏弱 -1（下方但走平）", dim2({"point": 3150.0, "ma20": 3200.0,
                                        "ma20_bias_pct": -1.56, "ma20_slope_pct": 0.05}), -1)

    # 硬闸移除后：弱势市下 seed_scan 照常跑完，不再被大盘趋势短路。
    sc = screener_mod.Screener(cfg, mk)
    down = {"index": {"point": 3150.0, "chg_pct": -1.5, "ma20": 3200.0,
                      "ma20_above": False, "ma20_bias_pct": -1.56, "ma20_slope_pct": -0.5},
            "stance": sent_mod.STANCE_DEFEND, "allow_new": True,
            "score": 45.0, "cycle": "修复"}
    res = sc.seed_scan(sent=down, include_eve=False, write_trace=False)
    t.ok("弱势市不再被大盘趋势硬闸短路",
         all("大盘趋势=0" not in n for n in (res.get("notes") or [])),
         str(res.get("notes")))
    t.ok("弱势市下扫描照常产出板块候选", bool(res.get("sectors")),
         f"sectors_n={len(res.get('sectors') or [])}")


def check_winrate_gate(t: Suite, cfg: Config, mk: FakeMarket) -> None:
    """胜率因子门槛：历史低胜率特征从「可买」降级观察。"""
    t.head("扫描 · 胜率因子门槛（阶段 A）")
    sc = screener_mod.Screener(cfg, mk)

    def gate(rank, stage, wr_score=None):
        ev = {"sector": {"rank": rank}, "stage": {"stage": stage}}
        if wr_score is not None:
            ev["winrate_score"] = wr_score
        return sc._winrate_gate(ev)

    t.ok("板块排名 6 > 5 → 降级", bool(gate(6, "萌芽", wr_score=5)))
    t.ok("板块排名 5 ≤ 5 → 放行（胜率分达标）", gate(5, "萌芽", wr_score=5) is None)
    t.ok("突破一律降级（即使排名 1）", bool(gate(1, "突破", wr_score=5)))
    t.ok("突破一律降级（排名 3）", bool(gate(3, "突破", wr_score=5)))
    t.ok("过热一律降级（即使排名 1）", bool(gate(1, "过热", wr_score=5)))
    t.ok("过热一律降级（排名 5）", bool(gate(5, "过热", wr_score=5)))
    t.ok("胜率分不足 → 降级", bool(gate(2, "萌芽", wr_score=2)))
    t.ok("排名缺失不因排名误杀（胜率分达标）", gate(None, "萌芽", wr_score=5) is None)

    # 入选板块一致性：筛入排名超限 / 与预审板块错位 → 降级
    def gate_pick(rank, stage, wr_score=5, pick_rank=None, pick_bk=None, cur_bk=None,
                  pick_name=None, cur_name=None):
        ev = {
            "sector": {"rank": rank, "bk": cur_bk, "name": cur_name},
            "stage": {"stage": stage},
            "winrate_score": wr_score,
            "pick_sector_rank": pick_rank,
            "pick_sector_bk": pick_bk,
            "pick_sector_name": pick_name,
        }
        return sc._winrate_gate(ev)

    t.ok("入选板块排名 11 > 5 → 降级",
         bool(gate_pick(3, "萌芽", pick_rank=11, pick_bk="BK1", cur_bk="BK1")))
    t.ok("入选/预审板块 bk 不一致 → 降级",
         bool(gate_pick(2, "萌芽", pick_rank=2, pick_bk="BK_A", cur_bk="BK_B",
                        pick_name="强板块", cur_name="弱板块")))
    t.ok("入选/预审一致且排名达标 → 放行",
         gate_pick(2, "萌芽", pick_rank=2, pick_bk="BK1", cur_bk="BK1") is None)

    # 关闭「突破一律否决」后，回退为「突破+排名>N」
    old_block = cfg.get("strategy.winrate_breakout_block")
    cfg.set("strategy.winrate_breakout_block", False)
    try:
        t.ok("兼容：突破+排名4>3 → 降级", bool(gate(4, "突破", wr_score=5)))
        t.ok("兼容：突破+排名3≤3 → 放行", gate(3, "突破", wr_score=5) is None)
    finally:
        cfg.set("strategy.winrate_breakout_block", old_block)

    # 关闭过热禁买 → 过热可按其他闸门放行
    old_oh = cfg.get("strategy.winrate_overheat_block")
    cfg.set("strategy.winrate_overheat_block", False)
    try:
        t.ok("关闭过热禁买后排名5+胜率分达标 → 放行",
             gate(5, "过热", wr_score=5) is None)
    finally:
        cfg.set("strategy.winrate_overheat_block", old_oh)

    # 总开关关闭 → 全部放行
    old = cfg.get("strategy.winrate_gate_enabled")
    cfg.set("strategy.winrate_gate_enabled", False)
    try:
        t.ok("关闭开关后排名 6 放行", gate(6, "过热") is None)
        t.ok("关闭开关后错位放行",
             gate_pick(2, "过热", pick_rank=11, pick_bk="A", cur_bk="B") is None)
    finally:
        cfg.set("strategy.winrate_gate_enabled", old)


def check_sector_tradeable(t: Suite, cfg: Config, mk: FakeMarket) -> None:
    """板块排序只统计可交易成员：创业板/北交所涨停不计入涨停家数。"""
    t.head("扫描 · 板块排序可交易性过滤")
    # 只开主板：模拟真实散户（未开通创业板/科创板/北交所），验证不可交易成员的
    # 涨停/温和票不计入板块结构分（否则会像 08-25 那样把买不了的板块选进 TOP）。
    saved_perm = dict(cfg.get("permissions") or {})
    for b in ("gem", "star", "bse"):
        cfg.set(f"permissions.{b}", False)
    orig_ranking = mk.get_sector_ranking
    orig_members = mk.get_sector_members
    mk.get_sector_ranking = lambda force=False: [
        {"bk": "BKTEST", "name": "测试板块", "chg": 6.0, "up_n": 10, "down_n": 0, "rank": 1}]
    mk.get_sector_members = lambda bk: [
        {"code": "600001", "name": "主板涨停", "chg": 9.9, "turnover": 5.0, "cap_yi": 80.0},
        {"code": "300001", "name": "创业板涨停", "chg": 19.9, "turnover": 5.0, "cap_yi": 80.0},
        {"code": "920001", "name": "北交所涨停", "chg": 29.9, "turnover": 5.0, "cap_yi": 80.0},
        {"code": "600002", "name": "主板温和", "chg": 4.0, "turnover": 5.0, "cap_yi": 80.0},
    ]
    try:
        sc = screener_mod.Screener(cfg, mk)
        step1 = sc.rank_sectors()
        top = step1["top"]
        t.ok("测试板块入池", bool(top), f"top={[(s.get('name'), s.get('gate')) for s in top]}")
        if top:
            t.eq("涨停只数只算可交易主板", top[0]["limit_up_count"], 1)
            t.eq("可交易成员数", top[0]["tradeable_n"], 2)
            t.eq("温和票只算可交易主板", top[0]["mild_n"], 1)
    finally:
        for k, v in saved_perm.items():
            cfg.set(f"permissions.{k}", v)
        mk.get_sector_ranking = orig_ranking
        mk.get_sector_members = orig_members


def check_lowbuy_pool(t: Suite, cfg: Config, mk: FakeMarket) -> None:
    """低吸板块池：排名 3~10、升温(2~4%)、涨停≤1 的板块。"""
    t.head("扫描 · 低吸（启动前夕）板块池")
    sc = screener_mod.Screener(cfg, mk)
    scored = [
        {"rank": 1, "chg": 6.5, "limit_up_count": 3, "name": "已涨停龙头"},
        {"rank": 2, "chg": 5.0, "limit_up_count": 2, "name": "涨停次强"},
        {"rank": 3, "chg": 3.5, "limit_up_count": 1, "name": "升温1"},
        {"rank": 5, "chg": 2.5, "limit_up_count": 0, "name": "升温2"},
        {"rank": 8, "chg": 3.0, "limit_up_count": 1, "name": "升温3"},
        {"rank": 10, "chg": 4.0, "limit_up_count": 1, "name": "升温4"},
        {"rank": 11, "chg": 3.0, "limit_up_count": 1, "name": "排名超限"},
        {"rank": 4, "chg": 5.5, "limit_up_count": 0, "name": "涨幅超限"},
        {"rank": 4, "chg": 1.0, "limit_up_count": 0, "name": "涨幅不足"},
        {"rank": 4, "chg": 3.0, "limit_up_count": 3, "name": "涨停过多"},
    ]
    pool = sc.lowbuy_sector_pool(scored)
    names = [s["name"] for s in pool]
    t.eq("低吸板块池数量", len(pool), 4)
    t.ok("低吸池含升温板块",
         all(n in names for n in ("升温1", "升温2", "升温3", "升温4")), str(names))
    t.ok("低吸池排除已涨停龙头", "已涨停龙头" not in names)
    t.ok("低吸池排除排名超限", "排名超限" not in names)
    t.ok("低吸池排除涨停过多", "涨停过多" not in names)


def check_seed(t: Suite, cfg: Config, mk: FakeMarket, sent: dict) -> dict:
    t.head("扫描 · 种子四步流（§9）")
    sc = screener_mod.Screener(cfg, mk)

    # 涨幅窗下限随最强板块自适应：固定 3% 在弱市里会把候选排空
    dyn = screener_mod.dynamic_min_chg
    t.eq("强市（最强板块 6%）下限保持 3.0%", dyn(6.0), 3.0)
    t.eq("边界：最强板块 5% 仍算强市", dyn(5.0), 3.0)
    t.eq("4%~5%：下限降至 2.5%", dyn(4.5), 2.5)
    t.eq("边界：最强板块 4% 降 0.5", dyn(4.0), 2.5)
    t.eq("边界：3.99% 落弱市地板", dyn(3.99), 2.0)
    t.eq("弱市（最强板块 3%）落地板 2.0%", dyn(3.0), 2.0)
    t.ok("地板 2.0% 不再下探（R:R 撑不住）",
         dyn(0.0) == 2.0 and dyn(-4.0) == 2.0, f"0%→{dyn(0.0)} -4%→{dyn(-4.0)}")
    t.eq("板块涨幅未知时不下调", dyn(None), 3.0)

    base_min = float(cfg.get("seed.strict_min_chg", 3.0))
    base_max = float(cfg.get("seed.strict_max_chg", 5.5))
    p_weak = screener_mod.tier_params(screener_mod.TIER_STRICT, cfg, 3.0)
    p_sprout = screener_mod.tier_params(screener_mod.TIER_SPROUT, cfg, 3.0)
    p_relaxed = screener_mod.tier_params(screener_mod.TIER_RELAXED, cfg, 3.0)
    p_mom = screener_mod.tier_params(screener_mod.TIER_MOMENTUM, cfg, 3.0)
    t.eq("严格档/萌芽档共享动态下限",
         (p_weak["min_chg"], p_sprout["min_chg"]), (2.0, 2.0))
    t.eq("热点降级档下限同步下调", p_relaxed["min_chg"], 2.0)
    t.eq("动量档不动态化（面向强势票）", p_mom["min_chg"],
         float(cfg.get("seed.momentum_min_chg", 5.0)))
    t.eq("下限下调不动严格档上限（窗口变宽）", p_weak["max_chg"], base_max)
    t.eq("不传板块涨幅时档位参数行为不变",
         screener_mod.tier_params(screener_mod.TIER_STRICT, cfg)["min_chg"], base_min)
    eve_mid = screener_mod.tier_params(screener_mod.TIER_EVE, cfg, 4.5)
    t.eq("前夕上限收到动态下限，不与严格窗重叠",
         (eve_mid["min_chg"], eve_mid["max_chg"]), (float(cfg.get("seed.eve_min_chg", 1.0)), 2.5))
    t.eq("强市下前夕窗保持 1%~3%",
         screener_mod.tier_params(screener_mod.TIER_EVE, cfg, 6.0)["max_chg"],
         float(cfg.get("seed.eve_max_chg", 3.0)))

    # 弱市回归：最强板块只涨 3.58% 的一天，2.86% 的前排票在固定 3% 下限里必被排空
    weak_members = [{"code": "600111", "name": "领涨一号", "chg": 5.10,
                     "turnover": 9.0, "cap_yi": 90.0, "rank": 1},
                    {"code": "600123", "name": "测试科技", "chg": 2.86,
                     "turnover": 8.5, "cap_yi": 120.0, "rank": 2}]
    weak_members += [{"code": f"6009{i:02d}", "name": f"弱势{i:02d}",
                      "chg": round(1.0 - i * 0.1, 2), "turnover": 2.0,
                      "cap_yi": 60.0, "rank": 3 + i} for i in range(8)]
    weak_sectors = [{"bk": "BK9001", "name": "弱市板块", "rank": 1, "chg": 3.58,
                     "limit_up_count": 0, "member_total": 10, "up_ratio": 1.0,
                     "mild_n": 1, "mild_ratio": 0.1, "total_score": 72.0,
                     "gate": "放宽", "members": weak_members}]
    weak_dyn = [c["code"] for c in sc.screen_tier(weak_sectors, screener_mod.TIER_STRICT,
                                                  max_sector_chg=3.58)]
    weak_fixed = [c["code"] for c in sc.screen_tier(weak_sectors, screener_mod.TIER_STRICT)]
    t.ok("弱市：2.86% 的前排票进入严格档", "600123" in weak_dyn, f"候选={weak_dyn}")
    t.ok("同一批数据在固定 3% 下限下被排空", "600123" not in weak_fixed,
         f"候选={weak_fixed}")

    # strongest_sector_chg / max_sector_chg 口径对齐：跳过严格档与动态窗口必须用同一个
    # 「最强板块涨幅」（排名表首位），不能一个 13.56% 一个 11.10%。
    _, _, notes_align = sc.screen_with_downgrade(weak_sectors, max_sector_chg=5.0,
                                                 strongest_sector_chg=7.0)
    t.ok("跳过严格档用 strongest_sector_chg（7.0%）而非 max_sector_chg（5.0%）",
         any("7.00%" in n and "跳过严格档" in n for n in notes_align),
         "；".join(notes_align))

    step1 = sc.rank_sectors()
    t.ok("第1步：板块池非空", bool(step1["top"]),
         f"入池 {len(step1['top'])} 个，合格 {len(step1.get('qualified') or [])} 个")
    top_names = [s["name"] for s in step1["top"]]
    t.ok("极热板块入池", HOT_NAME in top_names, f"top={top_names[:3]}")
    t.eq("最强板块涨幅回传", step1.get("strongest_sector_chg"),
         max(s["chg"] for s in SECTORS))
    t.eq("强市下温和票下限不变", step1.get("mild_chg_low"),
         float(cfg.get("seed.mild_chg_low", 3.0)))

    res = sc.seed_scan(sent=sent, include_eve=True, write_trace=True)
    win = res.get("dyn_window") or {}
    t.eq("扫描结果带动态窗口快照", (win.get("min_chg"), win.get("lowered")), (3.0, False))
    t.ok("扫描输出显示当前动态下限",
         "动态窗口：下限 3.0%" in seed_report.format_result(res, cfg),
         screener_mod.dyn_window_text(win))
    t.ok("扫描备注记下动态窗口",
         any("动态窗口" in n for n in (res.get("notes") or [])),
         "；".join(res.get("notes") or []))
    t.ok("四步流产出裁决", res["verdict"] in (screener_mod.VERDICT_TRADEABLE,
                                              screener_mod.VERDICT_PENDING,
                                              screener_mod.VERDICT_EMPTY),
         f"verdict={res['verdict']} 档位={res['tier']} 候选={res.get('candidates_n')}")
    codes = [e["code"] for e in (res["buyable"] + res["watch"] + res["near_miss"])]
    t.ok("目标股进入三档输出之一", TARGET in codes,
         f"可买={[e['code'] for e in res['buyable']]} "
         f"观察={[e['code'] for e in res['watch']]} "
         f"近失={[e['code'] for e in res['near_miss']]}")
    t.ok("可买档不超过上限", len(res["buyable"]) <= int(cfg.s("seed_max_output", 2)))
    t.ok("落选追溯已落盘", bool(res.get("trace")), f"trace={res.get('trace')}")

    # 候选明细：硬否决被 continue 丢弃后不进任何桶，只能靠这份明细看到淘汰原因
    t.eq("候选明细条数 = 初筛候选数", len(res.get("candidates") or []), res.get("candidates_n"))
    cand_codes = [c["code"] for c in (res.get("candidates") or [])]
    t.ok("跨板块去重：候选无重复代码", len(set(cand_codes)) == len(cand_codes),
         f"codes={cand_codes}")

    sec_ctx = {**{k: v for k, v in step1["top"][0].items() if k != "members"}, "found": True}
    mkcand = lambda code, name, chg: {
        "code": code, "name": name, "chg": chg, "sector": sec_ctx,
        "sector_name": sec_ctx.get("name"), "tier": screener_mod.TIER_STRICT,
        "pick": {"score": 70.0, "parts": {}}}
    vf = sc.veto_filter([mkcand("600111", "涨停一号", 9.95),
                         mkcand("600222", "涨停二号", 9.90),
                         mkcand(TARGET, TARGET_NAME, 5.20)], sent)
    det = screener_mod.finalize_candidates(vf["candidates"], vf["passed"])
    t.eq("3 只候选全进明细（含硬否决）", len(det), 3)
    t.ok("每条明细都带裁决与原因",
         all(d.get("verdict") and d.get("reason") for d in det),
         "；".join(f"{d['code']}[{d.get('verdict')}]" for d in det))
    hard = [d for d in det if d["verdict"] == screener_mod.CAND_HARD]
    t.eq("2 只涨停票 → 硬否决入明细", len(hard), 2)
    t.ok("硬否决原因带具体触发点", all("阈值" in (d.get("reason") or "") for d in hard),
         "；".join(d.get("reason") or "" for d in hard))
    fmt_det = seed_report.format_result({**res, "candidates": det, "candidates_n": len(det)}, cfg)
    t.ok("控制台候选明细每条都带原因行",
         all(f"        原因：{seed_report.cand_display_reason(d)}" in fmt_det for d in det),
         fmt_det[-20:] if len(fmt_det) > 20 else fmt_det)
    soft_stub = {"code": "002141", "name": "贤丰控股", "verdict": screener_mod.CAND_SOFT,
                 "veto_labels": ["MA20 乖离过热"], "reason": ""}
    screener_mod._finalize_cand_reason(soft_stub)
    t.ok("软否决 reason 空时由 veto_labels 兜底",
         "MA20" in (soft_stub.get("reason") or ""),
         soft_stub.get("reason"))
    md = seed_report.render_md({**res, "candidates": det, "candidates_n": 3}, cfg)
    t.ok("SEED 报告含候选明细表且覆盖全部候选",
         "候选明细" in md and all(d["code"] in md for d in det),
         f"codes={[d['code'] for d in det]}")
    t.ok("SEED 报告含共振六维逐项展开", "共振六维" in md, "")
    t.ok("共振六维含市值具体值", "市值 120.0亿" in md, "")
    t.ok("共振六维含量价结构条件与结果",
         "放量上涨" in md and "量比 1.80≥1.2" in md and "均线多头" in md, "")

    # 涨幅窗口复检：第 2 步按板块成分股的批量快照涨幅筛入，第 3 步拉到实时行情后
    # 要用最新涨幅再核一次；实时涨幅已移出窗口的候选不得再作为种子输出。
    old_q = mk.quotes.get(TARGET)
    mk.quotes[TARGET] = _quote(TARGET, TARGET_NAME, 12.00, 6.80, HOT_NAME,
                               high=12.20, low=11.50, open=11.60)
    try:
        cand_out = mkcand(TARGET, TARGET_NAME, 5.20)
        cand_out["win_min"], cand_out["win_max"] = 3.0, 5.5
        vf2 = sc.veto_filter([cand_out], sent)
        chg_out = [d for d in vf2["candidates"] if d["verdict"] == screener_mod.CAND_CHG_OUT]
        t.eq("实时涨幅移出窗口 → 不再作为种子输出",
             (len(chg_out), len(vf2["passed"]), len(vf2["soft"])), (1, 0, 0))
        t.ok("移出窗口原因带实时涨幅",
             chg_out and "6.80%" in (chg_out[0].get("reason") or ""),
             chg_out[0].get("reason") if chg_out else "无移出窗口明细")
    finally:
        mk.quotes[TARGET] = old_q
    return res


def check_plan_labels(t: Suite, cfg: Config, mk: FakeMarket, sent: dict) -> None:
    """给人看的计划提示必须带股票名。

    实测告警只印了 `['002882']`，还要再去查一遍这是哪只票。代码是给机器对齐的，
    planned_codes 的集合匹配语义不能改，所以另开 planned_labels；同时得盯住缺名字的
    情形——印成 "002882 None" 比光印代码更难看。
    """
    t.head("计划 · 用户可见提示带股票名")
    from .phases import IO
    from .runtime import runner

    plan_mod.save_plan({
        "version": plan_mod.PLAN_VERSION, "planned_date": utils.today_str(),
        "execute_date": utils.today_str(), "status": plan_mod.STATUS_PENDING,
        "items": [{"code": "002882", "name": "中元股份", "status": plan_mod.STATUS_PENDING},
                  {"code": "600312", "status": plan_mod.STATUS_PENDING}],
        "notes": [],
    }, cfg)
    cur = plan_mod.load_plan(cfg)
    labels = plan_mod.planned_labels(cur)
    t.eq("有 name → 代码 + 名称", labels[0], "002882 中元股份")
    t.eq("缺 name → 只回退到代码", labels[1], "600312")
    t.ok("任何一项都不带 None", all("None" not in s for s in labels), f"labels={labels}")
    t.eq("planned_codes 语义不变（纯代码，供集合匹配）",
         plan_mod.planned_codes(cur), ["002882", "600312"])
    t.ok("active_codes_equal 相同 code+execute_date 判等",
         plan_mod.active_codes_equal(cur, ["002882", "600312"],
                                     execute_date=utils.today_str()))
    t.ok("active_codes_equal 不同 code 判不等",
         not plan_mod.active_codes_equal(cur, ["002882"],
                                         execute_date=utils.today_str()))
    t.ok("active_codes_equal execute_date 不一致判不等（防旧计划误判）",
         not plan_mod.active_codes_equal(cur, ["002882", "600312"],
                                         execute_date="2020-01-01"))

    # 机器字段与展示字段各走一路：JSON 里的 plan_codes 不能因为排版好看而变形
    st = gates.status(sent, cfg)
    t.eq("status.plan_codes 仍是纯代码", st.get("plan_codes"), ["002882", "600312"])
    t.ok("今日状态行带股票名", "002882 中元股份" in gates.format_status(st),
         gates.format_status(st).splitlines()[3])

    # 门禁拦截文案：不在计划内时要说清“计划里到底是谁”
    gates.reset_state(cfg)
    g = gates.check_code_gate(TARGET, sent, cfg)
    detail = "；".join(b["detail"] for b in g.blocks if b["rule"] == "计划绑定")
    t.ok("计划绑定拦截文案含股票名", "002882 中元股份" in detail, detail or "未拦截")

    # 种子扫描无可买时的旧计划提醒（seed_max_output=0 强走“宁缺毋滥”分支）
    old_max = cfg.get("strategy.seed_max_output")
    cfg.set("strategy.seed_max_output", 0)
    io = IO(interactive=False, quiet=True)
    try:
        res = runner.seed_plan(cfg=cfg, market=mk, io=io, sent=sent, include_eve=False)
    finally:
        cfg.set("strategy.seed_max_output", old_max)
    t.eq("无可买 → 不写计划", res.get("plan"), None)
    warn = "\n".join(ln for ln in io.transcript if "仍存在未执行的旧计划" in ln)
    t.ok("旧计划提醒含股票名", "002882 中元股份" in warn, warn or "未出现提醒")
    t.ok("旧计划提醒不再是裸代码列表", "['002882']" not in warn, warn)

    plan_mod.clear_plan(cfg)
    gates.reset_state(cfg)


def check_plan_clear(t: Suite, cfg: Config) -> None:
    """plan-clear 必须真的存在，并且把旧计划清到底。

    种子扫描在“今日无可买”时会叫用户去执行 plan-clear，提示里点名的命令不存在
    比不提示更坏。这里同时盯住两件事：子命令注册到了 argparse，以及清除后
    is_valid_today 真的翻成假（否则计划绑定门禁还会拿旧计划放行）。
    """
    t.head("计划 · plan-clear 清除旧计划")
    from .runtime import cli

    args = cli.build_parser().parse_args(["plan-clear"])
    t.eq("plan-clear 已注册到 argparse", getattr(args, "func", None), cli.cmd_plan_clear)

    plan_mod.save_plan({
        "version": plan_mod.PLAN_VERSION, "planned_date": utils.today_str(),
        "execute_date": utils.today_str(), "status": plan_mod.STATUS_PENDING,
        "items": [{"code": "002882", "name": "中元股份", "status": plan_mod.STATUS_PENDING},
                  {"code": "600312", "status": plan_mod.STATUS_READY}],
        "notes": [],
    }, cfg)
    buf = io_mod.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(["plan-clear"])
    out = buf.getvalue()
    t.eq("plan-clear 退码 0", rc, 0)
    t.ok("确认信息带代码与名称", "已清除旧计划：002882 中元股份" in out,
         out.strip() or "（无输出）")
    t.ok("确认信息覆盖 ready_exec 项", "600312" in out, out.strip())

    cleared = plan_mod.load_plan(cfg)
    t.eq("整单状态 → cleared", cleared.get("status"), plan_mod.STATUS_CLEARED)
    t.eq("无残留待执行项", plan_mod.active_items(cleared), [])
    t.ok("记下了清除时间", bool(cleared.get("cleared_at")), str(cleared.get("cleared_at")))
    t.ok("清除理由写入备注",
         any("清除" in n for n in cleared.get("notes", [])), str(cleared.get("notes")))
    t.ok("清除后今日计划失效", not plan_mod.is_valid_today(cleared, cfg))

    # 空计划下重跑不能报错，也不能谎称清了东西
    buf2 = io_mod.StringIO()
    with contextlib.redirect_stdout(buf2):
        rc2 = cli.main(["plan-clear"])
    out2 = buf2.getvalue()
    t.eq("重跑仍退码 0", rc2, 0)
    t.ok("无计划时明确告知", "当前无待清除计划" in out2, out2.strip() or "（无输出）")
    t.ok("无计划时不报已清除", "已清除旧计划" not in out2, out2.strip())

    gates.reset_state(cfg)


def check_plan_check_clear_prompt(t: Suite, cfg: Config) -> None:
    """计划复核后的清除询问：只在真过期时弹，而且必须可以拒绝。

    这里盯三件事：过期判定不能误伤今日有效计划（否则复核完就把可执行计划
    问掉了）、答 y 才真清（且 is_valid_today 翻假）、非交互下不能阻塞也不能擅自改盘。
    """
    t.head("计划 · plan-check 过期清除询问")
    from .phases import IO
    from .runtime import cli

    def seed(execute_date: str, status: str) -> None:
        plan_mod.save_plan({
            "version": plan_mod.PLAN_VERSION, "planned_date": utils.today_str(),
            "execute_date": execute_date, "status": status,
            "items": [{"code": "002882", "name": "中元股份", "status": plan_mod.STATUS_PENDING}],
            "notes": [],
        }, cfg)

    yesterday = utils.today_str(utils.prev_trading_day())

    # 1. 今日有效计划：不弹提示
    seed(utils.today_str(), plan_mod.STATUS_PENDING)
    valid = plan_mod.load_plan(cfg)
    t.ok("今日有效计划不算过期", not cli._plan_expired(valid, cfg))
    io = IO(answers={"plan_clear": "y"}, quiet=True)
    t.ok("有效计划不询问清除", cli._offer_clear_expired_plan(cfg, io) is False)
    t.ok("有效计划复核后仍在", plan_mod.is_valid_today(plan_mod.load_plan(cfg), cfg))

    # 2. 执行日已过 + 答 y → 真清
    seed(yesterday, plan_mod.STATUS_PENDING)
    stale = plan_mod.load_plan(cfg)
    t.ok("执行日已过 → 过期", cli._plan_expired(stale, cfg))
    io = IO(answers={"plan_clear": "y"}, quiet=True)
    t.ok("答 y 执行清除", cli._offer_clear_expired_plan(cfg, io) is True)
    log = "\n".join(io.transcript)
    t.ok("询问文案点明已过期", "已过期" in log, log or "（无输出）")
    t.ok("确认信息带代码与名称", "已清除旧计划：002882 中元股份" in log, log)
    cleared = plan_mod.load_plan(cfg)
    t.eq("整单状态 → cleared", cleared.get("status"), plan_mod.STATUS_CLEARED)
    t.eq("无残留待执行项", plan_mod.active_items(cleared), [])
    t.ok("清除后今日计划失效", not plan_mod.is_valid_today(cleared, cfg))
    t.ok("已清除计划不再重复询问", not cli._plan_expired(cleared, cfg))

    # 3. 计划作废 + 答 n → 原状不动
    seed(utils.today_str(), plan_mod.STATUS_INVALID)
    io = IO(answers={"plan_clear": "n"}, quiet=True)
    t.ok("作废计划依旧询问", cli._plan_expired(plan_mod.load_plan(cfg), cfg))
    t.ok("答 n 不清除", cli._offer_clear_expired_plan(cfg, io) is False)
    t.ok("拒绝后告知保留", "已保留计划" in "\n".join(io.transcript), str(io.transcript))
    t.eq("拒绝后状态不变", plan_mod.load_plan(cfg).get("status"), plan_mod.STATUS_INVALID)

    # 4. 非交互（脚本调用）：不阻塞、不误删
    io = IO(interactive=False, quiet=True)
    t.ok("非交互不清除", cli._offer_clear_expired_plan(cfg, io) is False)
    t.eq("非交互不改盘", plan_mod.load_plan(cfg).get("status"), plan_mod.STATUS_INVALID)

    plan_mod.clear_plan(cfg)
    gates.reset_state(cfg)


def check_plan_recheck(t: Suite, cfg: Config) -> None:
    """计划复核的「共振分不足」必须指出具体哪一维/门槛变化，而非只报 6<7。

    复核输出如果只说「共振分 6 < 当前门槛 7」，复盘时无从知道是门槛涨了还是
    板块强度掉了。这里直接喂一组旧/新六维给 check_item，盯住展开文案。
    """
    t.head("计划 · 共振分不足展开到维度")
    dims = [
        {"no": 1, "name": "板块强度", "score": 2, "max": 2},
        {"no": 2, "name": "大盘趋势", "score": 1, "max": 1},
        {"no": 3, "name": "消息面", "score": 1, "max": 1},
        {"no": 4, "name": "市值区间", "score": 2, "max": 2},
        {"no": 5, "name": "量价结构", "score": 2, "max": 2},
        {"no": 6, "name": "止损结构", "score": 1, "max": 1},
    ]
    item = {
        "ref_price": 12.0,
        "snapshot": {
            "price": 12.0, "pass_threshold": 6,
            "scoring_dims": dims, "sector_rank": 1,
            "identity_tier": "龙头", "bias_ma20": 2.0,
        },
    }
    new_dims = [dict(d) for d in dims]
    new_dims[0]["score"] = 1  # 板块强度 2→1
    ev = {
        "veto": {},
        "quote": {"price": 12.0},
        "sector": {"rank": 1},
        "pass_threshold": 7,
        "total_score": 6,
        "levels": {"odds": 2.5},
        "identity": {"tier": "龙头"},
        "ind": {"bias_ma20": 3.0},
        "intraday": 0.5,
        "scoring": {"dims": new_dims},
    }
    changes = plan_mod.check_item(item, ev, cfg)
    kinds = sorted({c["kind"] for c in changes})
    t.eq("只触发共振分不足", kinds, ["共振分不足"])
    detail = changes[0]["detail"]
    t.ok("详情含维度变化（板块强度 2→1）", "板块强度 2→1" in detail, detail)
    t.ok("详情含门槛变化（6→7）", "门槛 6→7" in detail, detail)


def check_plan_buy_remove(t: Suite, cfg: Config, mk: FakeMarket) -> None:
    """计划买入（走持仓逻辑）+ 单条删除计划项。"""
    from .phases import IO
    from .runtime import runner

    t.head("计划 · 买入与单条删除")
    plan_mod.clear_plan(cfg)
    portfolio.remove_position(TARGET, cfg)
    plan_mod.save_plan({
        "version": plan_mod.PLAN_VERSION, "planned_date": utils.today_str(),
        "execute_date": utils.today_str(), "status": plan_mod.STATUS_PENDING,
        "items": [{"code": TARGET, "name": TARGET_NAME, "ref_price": 10.0,
                   "sl_pct": 6.0, "tp_pct": 15.0, "status": plan_mod.STATUS_PENDING},
                  {"code": "600999", "name": "待删票", "ref_price": 5.0,
                   "status": plan_mod.STATUS_PENDING}],
        "notes": [],
    }, cfg)

    io = IO(interactive=False, quiet=True)
    pos = runner.buy_plan_item(TARGET, 500, 10.0, cfg=cfg, market=mk, io=io)
    t.ok("计划买入生成持仓", pos is not None)
    t.eq("持仓股数按输入", pos["shares"], 500)
    t.eq("止损止盈沿用计划", (pos["sl_pct"], pos["tp_pct"]), (6.0, 15.0))
    item = plan_mod.find_item(plan_mod.load_plan(cfg), TARGET)
    t.eq("计划项标记 executed", item["status"], plan_mod.STATUS_EXECUTED)

    plan_mod.remove_item("600999", cfg)
    item2 = plan_mod.find_item(plan_mod.load_plan(cfg), "600999")
    t.eq("单条删除标记 removed", item2["status"], plan_mod.STATUS_REMOVED)
    t.eq("删除后不再 active", plan_mod.active_items(plan_mod.load_plan(cfg)), [])

    portfolio.remove_position(TARGET, cfg)
    plan_mod.clear_plan(cfg)


def check_leader_relax(t: Suite, cfg: Config) -> None:
    """龙头 -1 是门槛公式的一部分，但防守 +1 不被龙头 -1 抵消。

    陕西黑猫实例：防守市（情绪 47）里龙头 6/6 仍被选为可买——因为防守 +1 被龙头
    -1 抵消，门槛回到 6，防守等于没收紧。修法是龙头 -1 先算、防守 +1 后算，
    防守市里龙头也要 7 分卡。
    """
    t.head("门槛 · 龙头 -1 不抵消防守 +1")
    leader = {"tier": ident_mod.TIER_LEADER, "score": 70.0}
    follower = {"tier": ident_mod.TIER_FOLLOW, "score": 50.0}
    defend = {"stance": sent_mod.STANCE_DEFEND}

    th = preflight.effective_threshold(leader, defend, cfg=cfg)
    t.eq("防守姿态下龙头门槛 7（防守不抵消）", th["threshold"], 7)
    t.ok("防守 +1 留痕在 notes", any("防守姿态 +1" in n for n in th["notes"]), str(th["notes"]))

    th2 = preflight.effective_threshold(follower, defend, cfg=cfg)
    t.eq("防守姿态下跟风门槛 7（不 -1）", th2["threshold"], 7)

    th3 = preflight.effective_threshold(leader, None, cfg=cfg)
    t.eq("无防守时龙头门槛不破地板 → 6", th3["threshold"], 6)

    # 全链路：evaluate 对龙头在防守姿态的门槛为 7（龙头 -1 不抵消防守 +1）
    ev = preflight.evaluate(TARGET, FakeMarket(cfg), cfg, sent=defend)
    t.eq("evaluate 对龙头在防守姿态的门槛为 7", ev["pass_threshold"], 7)


def check_menu(t: Suite, cfg: Config) -> None:
    """菜单：默认只印四条建议；顶层压到约 10 项，其余收进子菜单。

    分组表与子菜单都是手工维护的，日后加/挪一个菜单项很容易忘了归组或漏掉
    功能——那个功能就从界面上消失，而且不报错。这里把两边对齐当成硬约束。
    """
    import datetime as _dt

    from .runtime import cli
    from .core.timing import Timing

    t.head("菜单 · 分组与时段建议")

    keys = [k for k, _, _ in cli.MENU]
    grouped = [k for _, ks in cli.MENU_GROUPS for k in ks]
    t.eq("顶层约 10 项", len(keys), 10)
    t.eq("顶层分组总数等于菜单项数", len(grouped), len(keys))
    t.ok("顶层分组无重复", len(set(grouped)) == len(grouped))
    t.ok("顶层分组无遗漏", set(grouped) == set(keys),
         f"缺 {sorted(set(keys) - set(grouped))} 多 {sorted(set(grouped) - set(keys))}")

    # 子菜单引用必须都真实存在，且全部功能一个不少（顶层 + 子菜单合并后）。
    submenu_refs = [av[1] for _, _, av in cli.MENU if av[0] == "__submenu__"]
    t.ok("子菜单引用都存在", set(submenu_refs) <= set(cli.SUBMENUS),
         f"引用 {submenu_refs} 缺失 {sorted(set(submenu_refs) - set(cli.SUBMENUS))}")
    expect_subs = {"计划", "准入", "持仓", "复盘工具", "配置与维护"}
    t.eq("五组子菜单齐全", set(cli.SUBMENUS), expect_subs)
    all_argv = [av[0] for _, _, av in cli.MENU if av[0] != "__submenu__"]
    for sub in cli.SUBMENUS.values():
        all_argv += [av[0] for _, _, av in sub]
    expect = {"weather", "status", "seed-plan", "winrate-scan", "plan-check", "__plan__",
              "run", "pos", "__close__", "watch", "review", "accum", "trace",
              "followthrough", "trades", "stats", "weekly", "__pos_add__", "__pos_rm__",
              "__confirm__", "__eval__", "config", "setup", "selftest", "plan-clear"}
    t.eq("25 项功能一个不少", sorted(set(all_argv)), sorted(expect))

    # 时段→建议：每个时段都得有东西可做，且不超过四条（否则就又回到平铺）。
    real_now = utils.now
    try:
        for stamp in ("2026-08-03 09:00", "2026-08-03 10:05", "2026-08-03 10:40",
                      "2026-08-03 12:17", "2026-08-03 14:10", "2026-08-03 14:50",
                      "2026-08-03 16:30", "2026-08-02 16:30"):
            fake = _dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
            utils.now = lambda when=None, _f=fake: _f
            ks = cli.suggest_keys(Timing(cfg), cfg)
            t.ok(f"{stamp} 建议 1-4 条且均合法",
                 1 <= len(ks) <= 4 and len(set(ks)) == len(ks) and all(k in keys for k in ks),
                 f"got={ks}")

        # 非交易日不能推荐新开：门禁必定拦回，推了就是领着人撞墙。
        holiday = _dt.datetime(2026, 8, 2, 16, 30)
        utils.now = lambda when=None, _f=holiday: _f
        t.ok("非交易日不推荐准入评估", "5" not in cli.suggest_keys(Timing(cfg), cfg))

        # 买入窗口必须推荐准入评估：一天就这 45 分钟能新开。
        window = _dt.datetime(2026, 8, 3, 14, 10)
        utils.now = lambda when=None, _f=window: _f
        t.ok("买入窗口推荐准入评估", "5" in cli.suggest_keys(Timing(cfg), cfg))
    finally:
        utils.now = real_now


def check_end_to_end(t: Suite, cfg: Config, mk: FakeMarket, sent: dict) -> None:
    t.head("主流程 · run_once 端到端（§14 验收 1）")
    from .runtime import runner
    from .phases import IO

    gates.reset_state(cfg)
    portfolio.set_capital(100000.0, cfg)
    ev = preflight.evaluate(TARGET, mk, cfg, sent=sent, has_news=True)
    t.eq("预审裁决 PASS", ev["verdict"], preflight.VERDICT_PASS)
    t.ok("共振分 ≥ 有效门槛", (ev["total_score"] or 0) >= (ev["pass_threshold"] or 99),
         f"{ev['total_score']}/{ev['pass_threshold']}")

    plan_mod.write_plan([ev], cfg, planned_date=utils.today_str(),
                        execute_date=utils.today_str())
    t.ok("今日计划有效", plan_mod.is_valid_today(plan_mod.load_plan(cfg), cfg))

    io = IO(answers={"code": TARGET, "has_news": True, "confirm_buy": True},
            interactive=False, quiet=True)
    out = runner.run_once(capital=100000.0, cfg=cfg, market=mk, io=io,
                          require_window=False, sent=sent)
    t.eq("最终决策 BUY", out["decision"], "BUY")
    t.eq("流程走完 4 个阶段", out["stage"], runner.STAGE_DONE)
    t.ok("报告已存档", bool(out.get("report_path")), f"{out.get('report_path')}")

    ps = portfolio.positions(cfg)
    t.eq("灰度仓已记录", len(ps), 1)
    if ps:
        sz = out["ctx"]["sizing"]
        t.eq("持仓股数 = 灰度股数", ps[0]["shares"], sz["gray_shares"])
        t.ok("确认仓待补", int(ps[0]["confirm_shares"]) > 0,
             f"confirm={ps[0]['confirm_shares']}")
    t.eq("单日新开计数 +1", gates.load_state(cfg).get("new_opens"), 1)
    t.eq("计划标记 executed",
         (plan_mod.find_item(plan_mod.load_plan(cfg), TARGET) or {}).get("status"),
         plan_mod.STATUS_EXECUTED)

    # 同一天第二次买入应被单日新开门禁拦下
    io2 = IO(answers={"code": TARGET, "has_news": True, "confirm_buy": True},
             interactive=False, quiet=True)
    out2 = runner.run_once(capital=100000.0, cfg=cfg, market=mk, io=io2,
                           require_window=False, sent=sent)
    t.ok("当日第二次评估被门禁拦截", out2["decision"] in ("REJECT", "CANCEL"),
         f"decision={out2['decision']} stage={out2['stage']}")

    # 确认仓 + 平仓闭环
    io3 = IO(interactive=False, quiet=True)
    pos = runner.confirm_position(TARGET, 12.5, cfg, mk, io3)
    t.ok("确认仓补足到满仓", bool(pos) and pos.get("stage") == portfolio.STAGE_FULL,
         f"shares={(pos or {}).get('shares')}")
    rec = runner.close_trade(TARGET, 13.5, "止盈", cfg, mk, io3)
    t.ok("平仓写入流水", bool(rec) and rec.get("pnl") is not None,
         f"pnl={(rec or {}).get('pnl')} R={(rec or {}).get('r_multiple')}")
    t.eq("持仓已清空", len(portfolio.positions(cfg)), 0)


def check_followthrough(t: Suite, cfg: Config) -> None:
    """跟涨样本：T+1 未回填计数 + (date, code) 去重 + 轨道升级保留。"""
    t.head("跟涨样本 · T+1 回填提示与去重")
    recs = [
        {"date": "2026-08-08", "code": "600001", "name": "历史未回填",
         "next_chg": None, "result": None},
        {"date": "2026-08-09", "code": "600002", "name": "历史已回填",
         "next_chg": 3.5, "result": "win"},
        {"date": utils.today_str(), "code": "600003", "name": "今日待回填",
         "next_chg": None, "result": None},
    ]
    ft_mod.save_records(recs, cfg)
    t.eq("未回填数只算历史日期（跳过今日）", ft_mod.pending_backfill(cfg), 1)

    r_dup = ft_mod.record_seed([{"code": "600001", "name": "历史未回填", "price": 10.0}],
                               cfg, date="2026-08-08")
    t.eq("record_seed 对已存在 (date,code) 去重跳过",
         r_dup, {"added": 0, "skipped": 1, "updated": 0})

    r_new = ft_mod.record_seed([{"code": "600999", "name": "新样本", "price": 20.0}],
                               cfg, date="2026-08-10")
    t.eq("record_seed 新 (date,code) 正常落盘",
         r_new, {"added": 1, "skipped": 0, "updated": 0})
    t.eq("新增后未回填历史数 +1", ft_mod.pending_backfill(cfg), 2)

    # 轨道升级：同一 (date, code) 先落低优先级轨道、午后升级为「可买」，应保留「可买」。
    r1 = ft_mod.record_seed([{"code": "600888", "name": "升级票", "price": 5.0,
                              "track": "观察轨", "tier": "热点降级档", "stage": "突破"}],
                            cfg, date="2026-08-11")
    t.eq("先落观察轨", r1, {"added": 1, "skipped": 0, "updated": 0})
    r2 = ft_mod.record_seed([{"code": "600888", "name": "升级票", "price": 5.1,
                              "track": "可买", "tier": "热点降级档", "stage": "突破"}],
                            cfg, date="2026-08-11")
    t.eq("后升级为可买", r2, {"added": 0, "skipped": 0, "updated": 1})
    up = next(r for r in ft_mod.load_records(cfg) if r.get("code") == "600888")
    t.eq("轨道升级为可买", up.get("track"), "可买")
    t.eq("升级后价格更新", up.get("close"), 5.1)

    # 反向：可买 → 观察轨 不应降级
    r3 = ft_mod.record_seed([{"code": "600888", "name": "升级票", "price": 5.0,
                              "track": "观察轨"}], cfg, date="2026-08-11")
    t.eq("可买不降级回观察轨", r3, {"added": 0, "skipped": 1, "updated": 0})
    up2 = next(r for r in ft_mod.load_records(cfg) if r.get("code") == "600888")
    t.eq("轨道仍为可买", up2.get("track"), "可买")

    # 新字段：市场天气 + 六维共振拆解随样本落盘（供「选了3天全跌」归因）
    r4 = ft_mod.record_seed([{
        "code": "600787", "name": "字段票", "price": 6.0, "track": "可买",
        "tier": "严格档", "stage": "突破", "total_score": 6, "pass_threshold": 6,
        "scoring_dims": [{"name": "板块强度", "score": 2, "max": 2}],
        "market_score": 47.0, "market_stance": "防守", "market_ma20_above": False,
        "bias_ma20": 12.5, "atr_pct": 5.2, "vol_ratio": 1.8,
        "turnover": 9.5, "intraday": 0.72,
        "amount_yi": 12.0, "cap_yi": 150.0, "rank_pct": 0.15,
        "odds": 2.4, "veto_labels": ["接近涨停", "分时偏高"],
    }], cfg, date="2026-08-12")
    t.eq("带市场/维度字段的样本落盘", r4, {"added": 1, "skipped": 0, "updated": 0})
    f = next(r for r in ft_mod.load_records(cfg) if r.get("code") == "600787")
    t.eq("六维共振随样本落盘", (f.get("scoring_dims") or [{}])[0].get("name"), "板块强度")
    t.eq("市场姿态随样本落盘", f.get("market_stance"), "防守")
    t.eq("大盘趋势随样本落盘", f.get("market_ma20_above"), False)
    t.eq("乖离随样本落盘", f.get("bias_ma20"), 12.5)
    t.eq("ATR%随样本落盘", f.get("atr_pct"), 5.2)
    t.eq("量比随样本落盘", f.get("vol_ratio"), 1.8)
    t.eq("换手随样本落盘", f.get("turnover"), 9.5)
    t.eq("分时位随样本落盘", f.get("intraday"), 0.72)
    t.eq("成交额随样本落盘", f.get("amount_yi"), 12.0)
    t.eq("市值随样本落盘", f.get("cap_yi"), 150.0)
    t.eq("板块内排名占比随样本落盘", f.get("rank_pct"), 0.15)
    t.eq("盈亏比随样本落盘", f.get("odds"), 2.4)
    t.eq("否决原因随样本落盘", f.get("veto_labels"), ["接近涨停", "分时偏高"])

    # 低吸/胜率字段必须落盘（否则样本计数永远为 0）
    r5 = ft_mod.record_seed([{
        "code": "600654", "name": "低吸字段票", "price": 8.0, "track": "前夕观察轨",
        "lowbuy": True, "winrate_score": 4, "mode": "rule",
        "ma_bull": True, "above_ma20": True, "sector_chg": 3.2,
        "sl_pct": 4.0, "tp_pct": 10.0,
        "pick_sector_bk": "BK001", "pick_sector_name": "入选测试板", "pick_sector_rank": 2,
    }], cfg, date="2026-08-13")
    t.eq("低吸/胜率字段样本落盘", r5, {"added": 1, "skipped": 0, "updated": 0})
    f5 = next(r for r in ft_mod.load_records(cfg) if r.get("code") == "600654")
    t.eq("lowbuy 标签落盘", f5.get("lowbuy"), True)
    t.eq("winrate_score 落盘", f5.get("winrate_score"), 4)
    t.eq("mode 落盘", f5.get("mode"), "rule")
    t.eq("ma_bull 落盘", f5.get("ma_bull"), True)
    t.eq("above_ma20 落盘", f5.get("above_ma20"), True)
    t.eq("sector_chg 落盘", f5.get("sector_chg"), 3.2)
    t.eq("sl_pct 落盘", f5.get("sl_pct"), 4.0)
    t.eq("tp_pct 落盘", f5.get("tp_pct"), 10.0)
    t.eq("pick_sector_bk 落盘", f5.get("pick_sector_bk"), "BK001")
    t.eq("pick_sector_name 落盘", f5.get("pick_sector_name"), "入选测试板")
    t.eq("pick_sector_rank 落盘", f5.get("pick_sector_rank"), 2)

    # 多周期回填：T+1/T+2/T+3/T+5 一次算好
    d = _end_dates(30)[10]
    ft_mod.save_records([{"date": d, "code": "600778", "name": "多周期样本",
                          "next_chg": None, "result": None}], cfg)
    mk = FakeMarket(cfg)
    upd = ft_mod.update_results(mk, cfg)
    t.ok("多周期回填了记录", upd.get("updated", 0) >= 1, str(upd))
    rec = next(r for r in ft_mod.load_records(cfg) if r.get("code") == "600778")
    t.ok("T+1 已回填", rec.get("next_chg") is not None, str(rec))
    t.ok("T+3 已回填", rec.get("chg_t3") is not None, str(rec))
    t.ok("T+5 已回填", rec.get("chg_t5") is not None, str(rec))
    t.ok("T+1 < T+3 < T+5（构造上行K线）",
         rec["next_chg"] < rec["chg_t3"] < rec["chg_t5"],
         f"t1={rec.get('next_chg')} t3={rec.get('chg_t3')} t5={rec.get('chg_t5')}")

    # 阶段 B 样本盘点：按 板块排名桶×阶段×档位×身份分桶 聚合，找最大桶
    ft_mod.save_records([
        {"date": "2026-08-15", "code": "600901", "name": "桶A1", "sector_rank": 1,
         "identity_score": 90.0, "stage": "过热", "tier": "热点降级档", "result": "win"},
        {"date": "2026-08-16", "code": "600902", "name": "桶A2", "sector_rank": 2,
         "identity_score": 91.0, "stage": "过热", "tier": "热点降级档", "result": "loss"},
        {"date": "2026-08-17", "code": "600903", "name": "桶A3", "sector_rank": 3,
         "identity_score": 88.0, "stage": "过热", "tier": "热点降级档", "result": "win"},
        {"date": "2026-08-18", "code": "600904", "name": "桶B1", "sector_rank": 9,
         "identity_score": 60.0, "stage": "突破", "tier": "严格档", "result": "loss"},
    ], cfg)
    st = ft_mod.stage_b_bucket_stats(cfg, min_samples=3)
    t.eq("阶段B最大桶样本", st["max_n"], 3)
    t.ok("阶段B最大桶达标（3≥3）", st["ready"])
    t.ok("阶段B最大桶键正确", st["max_bucket"].startswith("1-3 / 过热 / 热点降级档 / 85-93"),
         st["max_bucket"])
    st2 = ft_mod.stage_b_bucket_stats(cfg, min_samples=5)
    t.eq("阶段B未达标时还差条数", st2["threshold"] - st2["max_n"], 2)
    t.ok("阶段B未达标判定", not st2["ready"])

    # 低吸样本计数：lowbuy=True 的样本，含回填进度
    ft_mod.save_records([
        {"date": "2026-08-19", "code": "600905", "name": "低吸1", "lowbuy": True, "result": "win"},
        {"date": "2026-08-20", "code": "600906", "name": "低吸2", "lowbuy": True, "result": None},
        {"date": "2026-08-20", "code": "600907", "name": "追高1", "lowbuy": False, "result": "loss"},
    ], cfg)
    lb = ft_mod.lowbuy_sample_stats(cfg)
    t.eq("低吸样本总数", lb["total"], 2)
    t.eq("低吸样本已回填", lb["backfilled"], 1)
    t.eq("低吸样本胜", lb["wins"], 1)
    t.ok("低吸进度文案", "2 条" in ft_mod.format_lowbuy_status(cfg))

    # 影子标签：萌芽 ∪（非突破∧rank≤3）；只落盘对照，不驱动计划
    t.eq("萌芽打 shadow_tag",
         ft_mod.compute_shadow_tag("萌芽", 8), "萌芽")
    t.eq("前三非突破打标签",
         ft_mod.compute_shadow_tag("过热", 2), "前三非突破")
    t.eq("萌芽且前三合并标签",
         ft_mod.compute_shadow_tag("萌芽", None, 1), "萌芽|前三非突破")
    t.eq("突破不进前三非突破",
         ft_mod.compute_shadow_tag("突破", 1), None)
    t.eq("中游过热无标签",
         ft_mod.compute_shadow_tag("过热", 6), None)
    r_sh = ft_mod.record_seed([{
        "code": "600911", "name": "影子票", "price": 9.0, "track": "观察轨",
        "stage": "萌芽", "sector_rank": 2, "pick_sector_rank": 2,
    }], cfg, date="2026-08-21")
    t.eq("shadow_tag 随 record_seed 落盘", r_sh, {"added": 1, "skipped": 0, "updated": 0})
    f_sh = next(r for r in ft_mod.load_records(cfg) if r.get("code") == "600911")
    t.eq("落盘 shadow_tag 值", f_sh.get("shadow_tag"), "萌芽|前三非突破")

    ft_mod.save_records([
        {"date": "2026-08-10", "code": "601001", "stage": "萌芽", "sector_rank": 1,
         "chg_t3": 2.0, "result": "win"},
        {"date": "2026-08-11", "code": "601002", "stage": "过热", "pick_sector_rank": 2,
         "chg_t3": -1.0, "result": "loss"},
        {"date": "2026-08-12", "code": "601003", "stage": "突破", "sector_rank": 1,
         "chg_t3": 5.0, "result": "win"},
        {"date": "2026-08-13", "code": "601004", "stage": "萌芽", "sector_rank": 5,
         "chg_t3": None, "result": "loss"},
    ], cfg)
    cfg.set("followthrough.shadow_min_samples", 2)
    cfg.set("followthrough.t3_up_target", 0.60)
    sh = ft_mod.shadow_t3_stats(cfg)
    t.eq("影子桶有 T+3 条数（突破不算）", sh["n_t3"], 2)
    t.eq("影子桶 T+3>0 条数", sh["t3_up"], 1)
    t.eq("影子桶 T+3>0 胜率", sh["t3_up_rate"], 0.5)
    t.ok("未达 60% 门槛", not sh["ready"])
    t.ok("影子文案含目标", "60%" in ft_mod.format_shadow_status(cfg)
         or "≥60%" in ft_mod.format_shadow_status(cfg))

    # 样本缺口看板：待 T+1 / T+3、低吸、新闸门后可买
    today = utils.today_str()
    ft_mod.save_records([
        {"date": "2026-08-10", "code": "602001", "track": "观察轨", "result": None},
        {"date": "2026-08-11", "code": "602002", "track": "可买", "result": "loss",
         "chg_t3": None, "bias_ma20": 8.0},
        {"date": "2026-08-27", "code": "602003", "track": "可买", "result": "win",
         "chg_t3": 1.2, "bias_ma20": 3.0},
        {"date": today, "code": "602004", "track": "观察轨", "result": None,
         "lowbuy": True},
    ], cfg)
    gap = ft_mod.sample_gap_stats(cfg)
    t.eq("缺口：待 T+1", gap["pending_t1"], 1)
    t.eq("缺口：待 T+3", gap["pending_t3"], 1)
    t.eq("缺口：今日待下一交易日", gap["today_waiting"], 1)
    t.eq("缺口：新闸门后可买", gap["post_gate_buyable"], 1)
    t.eq("缺口：新闸门后可买已有 T+3", gap["post_gate_buyable_t3"], 1)
    t.eq("缺口：因子齐全数", gap["factor_ok"], 2)
    t.eq("缺口：低吸总数", gap["lowbuy_total"], 1)
    t.ok("缺口文案含待 T+1", "待 T+1" in ft_mod.format_sample_gap(cfg))

    # 自动回填开关：seed 触发轻量回填；关开关则跳过；menu 每天最多一次
    from .runtime import runner
    from .phases import IO
    import datetime as _dt

    t.eq("auto_backfill_on_seed 默认开",
         config_store.DEFAULTS["followthrough"]["auto_backfill_on_seed"], True)
    t.eq("auto_backfill_on_menu 默认开",
         config_store.DEFAULTS["followthrough"]["auto_backfill_on_menu"], True)
    t.eq("auto_backfill_full_review 默认关",
         config_store.DEFAULTS["followthrough"]["auto_backfill_full_review"], False)
    t.eq("auto_backfill_async 默认开",
         config_store.DEFAULTS["followthrough"]["auto_backfill_async"], True)
    t.eq("t3_up_target 默认 60%",
         config_store.DEFAULTS["followthrough"]["t3_up_target"], 0.60)
    t.eq("p0_gate_date 默认",
         config_store.DEFAULTS["followthrough"]["p0_gate_date"], "2026-08-26")

    d_hist = _end_dates(30)[8]
    ft_mod.save_records([{"date": d_hist, "code": "600778", "name": "自动回填样本",
                          "next_chg": None, "result": None, "chg_t5": None}], cfg)
    t.eq("自动回填前有 pending", ft_mod.pending_backfill(cfg), 1)
    io_q = IO(interactive=False, quiet=True)
    cfg.set("followthrough.auto_backfill_on_seed", False)
    t.ok("关 seed 开关则跳过",
         runner.maybe_auto_backfill(cfg=cfg, market=FakeMarket(cfg), io=io_q,
                                   trigger="seed", background=False) is None)
    cfg.set("followthrough.auto_backfill_on_seed", True)
    upd_auto = runner.maybe_auto_backfill(cfg=cfg, market=FakeMarket(cfg), io=io_q,
                                         trigger="seed", background=False)
    t.ok("seed 触发自动回填", upd_auto is not None and upd_auto.get("updated", 0) >= 1,
         str(upd_auto))
    t.eq("自动回填后 pending=0", ft_mod.pending_backfill(cfg), 0)

    # 异步回填：立即返回，后台线程完成后 pending 清零
    d_async = _end_dates(30)[7]
    ft_mod.save_records([{"date": d_async, "code": "600781", "name": "异步回填样本",
                          "next_chg": None, "result": None, "chg_t5": None}], cfg)
    t.eq("异步回填前有 pending", ft_mod.pending_backfill(cfg), 1)
    upd_bg = runner.maybe_auto_backfill(cfg=cfg, market=FakeMarket(cfg), io=io_q,
                                        trigger="seed", background=True)
    t.ok("异步立即返回", upd_bg is not None and upd_bg.get("async") is True,
         str(upd_bg))
    t.ok("异步回填线程结束", runner.wait_backfill_done(timeout=60))
    t.eq("异步回填后 pending=0", ft_mod.pending_backfill(cfg), 0)

    real_now = utils.now
    try:
        # 盘中：有 pending 也应跳过（不在盘后/隔夜窗）
        mid = _dt.datetime(2026, 8, 3, 11, 0)
        utils.now = lambda when=None, _f=mid: _f
        d_mid = _end_dates(30)[8]
        ft_mod.save_records([{"date": d_mid, "code": "600779", "name": "菜单回填样本",
                              "next_chg": None, "result": None, "chg_t5": None}], cfg)
        gates.reset_state(cfg)
        t.ok("盘中 menu 自动回填跳过",
             runner.maybe_auto_backfill(cfg=cfg, market=FakeMarket(cfg), io=io_q,
                                       trigger="menu", background=False) is None)
        # 盘后：日期与 FakeMarket K 线同一日历，应回填并打每日戳
        after = _dt.datetime(2026, 8, 3, 16, 30)
        utils.now = lambda when=None, _f=after: _f
        d_after = _end_dates(30)[8]
        ft_mod.save_records([{"date": d_after, "code": "600779", "name": "菜单回填样本",
                              "next_chg": None, "result": None, "chg_t5": None}], cfg)
        gates.reset_state(cfg)
        upd_m = runner.maybe_auto_backfill(cfg=cfg, market=FakeMarket(cfg), io=io_q,
                                          trigger="menu", background=False)
        t.ok("盘后 menu 自动回填", upd_m is not None and upd_m.get("updated", 0) >= 1,
             str(upd_m))
        t.ok("daily_state 打了 menu 回填戳",
             gates.load_state(cfg).get("auto_backfill_menu") is True)
        # 同日第二次应跳过（即使再塞 pending）
        ft_mod.save_records([{"date": d_after, "code": "600780", "name": "二次样本",
                              "next_chg": None, "result": None, "chg_t5": None}], cfg)
        t.ok("同日 menu 第二次跳过",
             runner.maybe_auto_backfill(cfg=cfg, market=FakeMarket(cfg), io=io_q,
                                       trigger="menu", background=False) is None)
    finally:
        utils.now = real_now
        cfg.set("followthrough.auto_backfill_on_seed", True)


def check_pricetrack(t: Suite, cfg: Config, mk: FakeMarket) -> None:
    """价格跟踪：进入种子文档 → 每日记价 → 卖出/删除停止。"""
    from .analysis import pricetrack

    t.head("跟踪 · 种子标的价格跟踪")
    pricetrack.save({}, cfg)
    added = pricetrack.ensure_tracked([TARGET, "601015"],
                                      {TARGET: TARGET_NAME, "601015": "陕西黑猫"}, cfg)
    t.eq("新纳入 2 只", added, 2)

    res = pricetrack.record_daily(mk, cfg)
    t.eq("记录 2 只当日价", res["recorded"], 2)
    res2 = pricetrack.record_daily(mk, cfg)
    t.eq("同日幂等（跳过 2 只）", (res2["recorded"], res2["skipped"]), (0, 2))

    pricetrack.mark_sold(TARGET, cfg)
    t.eq("卖出后标记 sold", pricetrack.load(cfg)[TARGET]["status"], pricetrack.STATUS_SOLD)
    t.eq("仍在跟踪 1 只", pricetrack.tracking_codes(cfg), ["601015"])

    pricetrack.mark_removed("601015", cfg)
    t.eq("删除后跟踪清空", pricetrack.tracking_codes(cfg), [])


def check_config_migration(t: Suite, home: str) -> None:
    """单源→多源的一次性配置迁移。

    只改 DEFAULTS 治不了已经落盘的配置：用户手里的 tea_config.json 把当时的
    单源名单钉死了，于是「5 源降级已实现」与「用户仍满屏网络抖动」同时成立。
    三件事要分开验：旧配置确实升上去了、用户自己选的组合不被覆盖、重复调用不重复升级。

    对应任务里的：test_legacy_config_multisource_migration_upgrades_to_five /
    test_migration_respects_user_choice / test_migration_is_idempotent。
    """
    t.head("配置 · 单源→多源迁移")
    ALL = list(config_store.ALL_DATA_SOURCES)

    # ---- 旧配置（单源 + retries=4）升到全五源
    legacy = {"market": {"data_sources": ["eastmoney"], "retries": 4}}
    out, changed = config_store._migrate_v1_to_multisource(legacy)
    t.ok("旧配置报告发生了升级", changed is True)
    t.eq("旧配置升到全五源", out["market"]["data_sources"], ALL)
    t.eq("旧默认重试 4 次同时削到 2", out["market"]["retries"], 2)
    t.ok("升级后打上迁移标记", out["meta"]["multisource_migrated"] is True)

    # 连 data_sources 那一项都没有的更老配置（用户盘上就是这种）也要升
    out2, changed2 = config_store._migrate_v1_to_multisource({"market": {"retries": 4}})
    t.ok("缺 data_sources 的老配置也升级",
         changed2 and out2["market"]["data_sources"] == ALL,
         str(out2["market"].get("data_sources")))

    # ---- 用户显式选的组合不能被覆盖（包括自己调过的重试次数）
    picked = {"market": {"data_sources": ["eastmoney", "tencent"], "retries": 6}}
    out3, changed3 = config_store._migrate_v1_to_multisource(picked)
    t.ok("显式配的源组合不报变更", changed3 is False)
    t.eq("显式配的源组合原样保留",
         out3["market"]["data_sources"], ["eastmoney", "tencent"])
    t.eq("用户调过的重试次数不动", out3["market"]["retries"], 6)
    t.ok("尊重用户选择后也不再看第二眼",
         out3["meta"]["multisource_migrated"] is True)

    # ---- 幂等：重复调用不重复升级，用户之后改回单源也不被反复掩回去
    again, changed4 = config_store._migrate_v1_to_multisource(out)
    t.ok("重复调用不再报变更", changed4 is False)
    t.eq("重复调用不改动任何值", again["market"]["data_sources"], ALL)
    reverted = {"meta": {"multisource_migrated": True},
                "market": {"data_sources": ["eastmoney"]}}
    out5, changed5 = config_store._migrate_v1_to_multisource(reverted)
    t.ok("已迁移过就不再强推五源",
         changed5 is False and out5["market"]["data_sources"] == ["eastmoney"],
         str(out5["market"]["data_sources"]))

    # ---- 走一道 load_config：提示只出一次，且真的回写了磁盘
    legacy_path = os.path.join(home, "legacy_config.json")
    utils.write_json(legacy_path, {"meta": {"initialized": True},
                                   "market": {"data_sources": ["eastmoney"], "retries": 4}})
    buf = io_mod.StringIO()
    with contextlib.redirect_stdout(buf):
        cfg1 = config_store.load_config(path=legacy_path)
    first = buf.getvalue()
    t.eq("load_config 升级到五源", cfg1.get("market.data_sources"), ALL)
    t.ok("升级时明确告知用户", "5 源降级" in first, first.strip() or "（无输出）")
    # 提示本身是多行文案：把它当序列直接 print 会把括号引号摆到用户眼前（tuple repr）
    t.ok("提示是自然语言而不是 tuple repr",
         "(" not in first and "')" not in first
         and "✓ 已自动启用" in first and "data_sources" in first,
         first.strip() or "（无输出）")
    t.eq("提示分两行打", len(first.strip().split("\n")), 2)
    # 内网只放通东财的用户被推上五源反而更慢，得告诉他怎么关掉其他四家
    t.ok("提示给出退回单源的具体写法",
         'market.data_sources 改回 ["eastmoney"]' in first, first.strip())
    saved = utils.read_json(legacy_path, default={}) or {}
    t.eq("升级已回写磁盘", saved.get("market", {}).get("data_sources"), ALL)
    t.eq("磁盘上的重试次数也跟着降", saved.get("market", {}).get("retries"), 2)
    buf2 = io_mod.StringIO()
    with contextlib.redirect_stdout(buf2):
        config_store.load_config(path=legacy_path)
    t.ok("提示只出一次（第二次启动不再吐）", buf2.getvalue().strip() == "",
         buf2.getvalue().strip())

    # 没有配置文件时不该凭空写出一份（首次启动向导还没跑）
    fresh_path = os.path.join(home, "fresh_config.json")
    buf3 = io_mod.StringIO()
    with contextlib.redirect_stdout(buf3):
        fresh = config_store.load_config(path=fresh_path)
    t.ok("无配置文件时不迁移也不落盘",
         not os.path.exists(fresh_path) and buf3.getvalue().strip() == "",
         buf3.getvalue().strip())
    t.eq("新用户直接拿到五源默认", fresh.get("market.data_sources"), ALL)

    # ---- 文档：内网用户要能自己查到要放通哪些域名（否则只能猜）
    readme = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "README.md")
    if os.path.exists(readme):
        with open(readme, encoding="utf-8") as fh:
            doc = fh.read()
        hosts = ("push2.eastmoney.com", "qt.gtimg.cn", "web.ifzq.gtimg.cn",
                 "hq.sinajs.cn", "money.finance.sina.com.cn",
                 "api.money.126.net", "api.finance.ifeng.com")
        t.ok("README 列齐五家要放通的域名",
             "### 网络要求" in doc and all(h in doc for h in hosts),
             str([h for h in hosts if h not in doc]))
        t.ok("README 点明新浪的 Referer 硬要求与内网退单源建议",
             "Referer" in doc and 'market.data_sources \'["eastmoney"]\'' in doc)
    else:
        t.ok("README 网络要求小节", True, "源码树外运行，无 README 可验")
        t.ok("README Referer 与退单源建议", True, "源码树外运行，无 README 可验")


def check_onboarding(t: Suite, home: str) -> None:
    """首次启动向导：能落盘、能校验、且第二次不再默默弹出。

    向导只跑一次，跑错了没人会发现：标记没打上就每次启动都问一遍，
    标记打了但值没写进去就是默默沿用默认值。两边都得验。
    """
    from .config import onboarding
    from .phases import IO

    t.head("首次启动 · 配置向导")

    # 独立的配置文件 + 独立数据目录，不碰其余检查的沙盒状态
    cfg_path = os.path.join(home, "wizard_config.json")
    cfg = Config({"paths": {"data_dir": "wizard_data"}}, path=cfg_path)
    t.ok("配置文件不存在时判为首次运行", onboarding.is_first_run(cfg))
    cfg.save()
    t.ok("无 initialized 标记仍判为首次运行", onboarding.is_first_run(cfg))

    t.eq("全角数字归一", utils.normalize_digits("５．８"), "5.8")

    # 逐项自定义：全角输入 / 非法值 / 回车取默认三种情形混在一起
    io = IO(answers={
        "wizard_mode": "2",
        "capital": "200000",
        "max_position_pct": "0.4",
        "strict_min_chg": "２．５",      # 全角
        "strict_max_chg": "6.5",
        "cap_min": "40",
        "cap_max": "400",
        "min_odds": "2.5",
        "pass_threshold": "7",
        "perm_main": True, "perm_gem": True, "perm_star": False, "perm_bse": False,
        "data_sources": "1",           # 全开五源降级链
        "wizard_confirm": "y",
    }, interactive=False, quiet=True)
    res = onboarding.run_wizard(cfg=cfg, io=io, first_run=True)
    t.eq("向导完成并落盘", (res.get("mode"), res.get("saved")), ("custom", True))

    saved = utils.read_json(cfg_path, default={}) or {}
    t.eq("单笔最大仓位已写入", saved.get("strategy", {}).get("max_position_pct"), 0.4)
    t.eq("涨幅下限（全角输入）已写入", saved.get("seed", {}).get("strict_min_chg"), 2.5)
    t.eq("涨幅上限已写入", saved.get("seed", {}).get("strict_max_chg"), 6.5)
    t.eq("市值区间已写入",
         (saved.get("seed", {}).get("cap_min"), saved.get("seed", {}).get("cap_max")),
         (40.0, 400.0))
    t.eq("R:R 门槛已写入", saved.get("strategy", {}).get("min_odds"), 2.5)
    t.eq("共振分门槛取整写入", saved.get("strategy", {}).get("pass_threshold"), 7)
    t.eq("未开通的板块已关闭",
         (saved.get("permissions", {}).get("star"), saved.get("permissions", {}).get("bse")),
         (False, False))
    t.eq("总资金已记入资金状态", portfolio.get_capital(cfg), 200000.0)
    t.eq("数据源降级链已写入", saved.get("market", {}).get("data_sources"),
         ["eastmoney", "tencent", "sina", "netease", "ifeng"])

    # 标记与幂等：同一份配置重新读起来也不能再弹向导
    t.ok("已打 initialized 标记", saved.get("meta", {}).get("initialized") is True)
    t.ok("标记了向导版本",
         saved.get("meta", {}).get("wizard_version") == onboarding.WIZARD_VERSION)
    reread = Config(utils.read_json(cfg_path, default={}) or {}, path=cfg_path)
    t.ok("第二次启动不再判为首次", not onboarding.is_first_run(reread))
    t.ok("第二次启动不再跑向导",
         onboarding.maybe_run(reread, IO(interactive=False, quiet=True)) is False)

    # 一键默认通道：写入值必须与 DEFAULTS 一致（向后兼容）
    cfg2 = Config({"paths": {"data_dir": "wizard_data2"}},
                  path=os.path.join(home, "wizard_config2.json"))
    res2 = onboarding.run_wizard(cfg=cfg2, io=IO(interactive=False, quiet=True),
                                 use_defaults=True)
    d = config_store.DEFAULTS
    t.ok("一键默认已落盘", res2.get("saved") is True)
    t.eq("默认通道不改变涨幅区间",
         (cfg2.get("seed.strict_min_chg"), cfg2.get("seed.strict_max_chg")),
         (d["seed"]["strict_min_chg"], d["seed"]["strict_max_chg"]))
    t.eq("默认通道不改变仓位上限",
         cfg2.get("strategy.max_position_pct"), d["strategy"]["max_position_pct"])
    t.ok("默认通道也打标记", not onboarding.is_first_run(cfg2))
    t.eq("默认通道跟随 DEFAULTS 的源名单", cfg2.get("market.data_sources"),
         d["market"]["data_sources"])
    t.eq("向导回车不会把用户送回单源",
         onboarding.SOURCE_PRESETS[0]["value"], d["market"]["data_sources"])
    t.eq("向导默认选项指向全开", onboarding.SOURCE_DEFAULT_CHOICE,
         onboarding.SOURCE_PRESETS[0]["choice"])

    # 跳过：不改任何值，但不再默默重弹
    cfg3 = Config({"paths": {"data_dir": "wizard_data3"}},
                  path=os.path.join(home, "wizard_config3.json"))
    res3 = onboarding.run_wizard(cfg=cfg3, io=IO(answers={"wizard_mode": "s"},
                                                interactive=False, quiet=True))
    t.eq("跳过不写入参数", (res3.get("mode"), res3.get("saved")), ("skip", False))
    t.ok("跳过仍打标记（不再纠缠）", not onboarding.is_first_run(cfg3))


# ==================================================================== 路径 / 打包

def check_paths(t: Suite) -> None:
    """运行时数据目录三级优先级：$TEA_HOME > ~/.tea/（打包版）> CWD（源码版）。

    打包成 .app / .exe 后可执行文件内部不可写，又不能因此改变源码运行的落盘
    位置（用户现有的 ./data 得原地不动），所以两种形态要分开验。
    """
    t.head("路径 · 运行时数据目录")
    old_home = os.environ.get(paths.HOME_ENV)
    old_cfg = os.environ.get(paths.CONFIG_ENV)
    old_frozen = getattr(sys, "frozen", None)
    # 两个分支会真的在 $HOME 下建目录（正是被测行为），记下原本存不存在，
    # 自测结束后把自己建的空目录清掉，不往用户家目录里留渣。
    probe = os.path.join(os.path.expanduser("~"), ".tea_expand_probe")
    user_dir = os.path.join(os.path.expanduser("~"), paths.USER_DIR)
    pre_existing = {p for p in (probe, user_dir) if os.path.isdir(p)}

    def _restore() -> None:
        for k, v in ((paths.HOME_ENV, old_home), (paths.CONFIG_ENV, old_cfg)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if old_frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = old_frozen
        for p in (probe, user_dir):
            if p in pre_existing:
                continue
            try:
                os.rmdir(p)        # 只能删空目录：里面真有数据就不碰
            except OSError:
                pass

    try:
        tmp = tempfile.mkdtemp(prefix="tea_paths_")
        os.environ.pop(paths.CONFIG_ENV, None)

        # ---- ① $TEA_HOME 最优先（源码版）
        os.environ[paths.HOME_ENV] = tmp
        if hasattr(sys, "frozen"):
            del sys.frozen
        t.eq("TEA_HOME 直接定位基准目录", str(paths.data_dir()), tmp)
        t.eq("配置落在基准目录下", str(paths.config_path()),
             os.path.join(tmp, paths.CONFIG_NAME))

        # ---- ② $TEA_HOME 即使在打包版也压过 ~/.tea（用户显式指定优先）
        sys.frozen = True
        t.eq("打包版下 TEA_HOME 仍然最优先", str(paths.data_dir()), tmp)
        t.ok("is_frozen 识别打包运行", paths.is_frozen() is True)

        # ---- ③ 打包版无 TEA_HOME → ~/.tea/
        os.environ.pop(paths.HOME_ENV, None)
        t.eq("打包版默认写 ~/.tea/", str(paths.data_dir()), user_dir)
        t.ok("~/.tea/ 自动创建", os.path.isdir(user_dir))

        # ---- ④ 源码版无 TEA_HOME → CWD（历史行为不变）
        del sys.frozen
        t.eq("源码版默认回落 CWD", str(paths.data_dir()), os.getcwd())
        t.ok("源码版不自称打包运行", paths.is_frozen() is False)

        # ---- ⑤ $TEA_CONFIG 跌过基准目录
        explicit = os.path.join(tmp, "elsewhere.json")
        os.environ[paths.CONFIG_ENV] = explicit
        t.eq("TEA_CONFIG 直指配置文件", str(paths.config_path()), explicit)
        os.environ.pop(paths.CONFIG_ENV, None)

        # ---- ⑥ ~ 展开（用户手写 TEA_HOME=~/x 时 shell 不一定帮展）
        os.environ[paths.HOME_ENV] = os.path.join("~", ".tea_expand_probe")
        t.eq("TEA_HOME 里的 ~ 会展开", str(paths.data_dir()), probe)

        # ---- ⑦ 转发一致：config_store 不再自己算路径
        os.environ[paths.HOME_ENV] = tmp
        t.eq("config_store.home_dir 走 paths", config_store.home_dir(), str(paths.data_dir()))
        t.eq("config_store.config_path 走 paths",
             config_store.config_path(), str(paths.config_path()))
        t.eq("环境变量常量同一份",
             (config_store.HOME_ENV, config_store.CONFIG_ENV, config_store.CONFIG_NAME),
             (paths.HOME_ENV, paths.CONFIG_ENV, paths.CONFIG_NAME))

        # ---- ⑧ Config 实际落盘仍在基准目录下的 data/ 子目录
        c = Config({}, path=os.path.join(tmp, "probe.json"))
        t.eq("data 子目录拼在基准目录下", c.data_dir(), os.path.join(tmp, "data"))
        t.ok("data_file 落在沙箱内",
             c.data_file("watch_pool_file").startswith(tmp), c.data_file("watch_pool_file"))
    finally:
        _restore()


def check_packaging(t: Suite) -> None:
    """打包规格的隐式导入清单必须盖住磁盘上所有模块。

    tea 大量走「按字符串取实现」（data.providers 按 market.data_sources 选源、
    phases 由菜单分派），PyInstaller 静态分析看不到，漏一个就是用户点到那个
    菜单才 ImportError 崩——而且只在打包版上复现。新增模块忘了同步 spec 时，
    这一条当场变红，不用等到发给用户。
    """
    t.head("打包 · spec 隐式导入")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec_path = os.path.join(root, "packaging", "tea.spec")
    if not os.path.exists(spec_path):
        t.ok("packaging/tea.spec 存在", False, spec_path)
        return
    t.ok("packaging/tea.spec 存在", True)
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = f.read()

    # 磁盘上的真实模块名（__init__.py 折成包名）
    mods = set()
    pkg_root = os.path.join(root, "tea")
    for dirpath, dirnames, filenames in os.walk(pkg_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            parts = rel[:-3].split(os.sep)
            if parts[-1] == "__init__":
                parts.pop()
            mods.add(".".join(parts))
    missing = sorted(m for m in mods if f'"{m}"' not in spec)
    t.ok(f"{len(mods)} 个模块全在 hiddenimports 里",
         not missing, "漏：" + ", ".join(missing) if missing else "")
    t.ok("数据源子包逐个列出",
         all(f'"tea.data.providers.{p}"' in spec
             for p in ("eastmoney", "tencent", "sina", "netease", "ifeng")))

    # macOS bundle 与控制台：交互式 CLI 不能被打成哑巴
    t.ok("bundle id 为 com.dataosir.tea", '"com.dataosir.tea"' in spec)
    t.ok("CFBundleName = TEA", '"CFBundleName": APP_NAME' in spec and 'APP_NAME = "TEA"' in spec)
    t.ok("LSBackgroundOnly 为 False", '"LSBackgroundOnly": False' in spec)
    t.ok("入口点是 tea/__main__.py", '"tea", "__main__.py"' in spec)
    t.ok("默认保留控制台", 'CONSOLE = os.environ.get("TEA_NO_CONSOLE") != "1"' in spec)
    t.ok("测试/科计包已排除",
         all(f'"{x}"' in spec for x in ("pytest", "ruff", "numpy")))


# ==================================================================== 入口

def main(verbose: bool = True, cfg: Optional[Config] = None) -> int:
    """在临时 TEA_HOME 下跑全部检查，不触碰真实数据目录。"""
    tmp = tempfile.mkdtemp(prefix="tea_selftest_")
    old_home = os.environ.get(config_store.HOME_ENV)
    os.environ[config_store.HOME_ENV] = tmp
    try:
        c = config_store.load_config(reload=True)
        # 自测环境：允许非交易日/非窗口运行，其余阈值全部用默认值
        c.set("timing.allow_weekend_ops", True)
        c.set("strategy.block_off_window_eval", False)
        c.set("strategy.require_standard_window_for_buy", False)
        c.set("report.write_seed_trace", True)
        c.save()

        t = Suite(verbose)
        if verbose:
            print("=" * 56)
            print(f"XJB_TRADE 离线自测（沙盒 {tmp}）")
            print(f"可调参数 {config_store.count_params()} 个")
            print("=" * 56)

        sent_mod.clear_cache()
        mk = FakeMarket(c)
        check_raw_parsing(t, c)
        check_clist_paging(t, c)
        check_disk_fallback(t, c)
        check_ztpool_fallback(t, c)
        check_host_failover(t, c)
        check_providers(t, c)
        sent = check_sentiment(t, c, mk)
        idn = check_identity(t, c, mk)
        lv = check_levels(t, c, mk)
        check_scoring(t, c, mk, sent, lv)
        check_winrate_score(t, c)
        check_veto(t, c, mk, idn)
        check_position(t, c)
        check_manual_position(t, c, mk)
        check_colors(t)
        check_gates(t, c, mk, sent)
        check_seed(t, c, mk, sent)
        check_market_trend_deduction(t, c, mk)
        check_winrate_gate(t, c, mk)
        check_sector_tradeable(t, c, mk)
        check_lowbuy_pool(t, c, mk)
        check_plan_labels(t, c, mk, sent)
        check_plan_clear(t, c)
        check_plan_check_clear_prompt(t, c)
        check_plan_recheck(t, c)
        check_plan_buy_remove(t, c, mk)
        check_leader_relax(t, c)
        check_menu(t, c)
        check_config_migration(t, tmp)
        check_onboarding(t, tmp)
        check_paths(t)
        check_packaging(t)
        check_end_to_end(t, c, mk, sent)
        check_followthrough(t, c)
        check_pricetrack(t, c, mk)
        return t.report()
    finally:
        sent_mod.clear_cache()
        if old_home is None:
            os.environ.pop(config_store.HOME_ENV, None)
        else:
            os.environ[config_store.HOME_ENV] = old_home
        config_store.load_config(reload=True)


if __name__ == "__main__":
    raise SystemExit(main())
