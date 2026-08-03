"""selftest.py — 离线自测：不联网，用假行情验证核心公式与门禁是否与规格对齐。

每一项断言都在测试内按规格文档独立重算一遍，再和引擎输出比对，
所以它能抓住"实现和规格分叉"的问题，而不只是"代码没崩"。

    python -m tea selftest
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

from . import expectancy as exp_mod
from . import config_store, gates, identity as ident_mod, portfolio, preflight
from . import plan as plan_mod
from . import screener as screener_mod
from . import sentiment as sent_mod
from . import utils, veto as veto_mod
from .config_store import Config
from .data import Market, indicators
from .data.fetcher import Fetcher

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

def _klines(n: int = 30, start: float = 10.0, step: float = 0.06,
            half_range: float = 0.008) -> List[dict]:
    """构造温和上行的日 K：MA5≥MA10≥MA20，且 ATR 可算。"""
    out = []
    for i in range(n):
        close = round(start + step * i, 4)
        out.append({
            "date": f"2026-{(i // 28) + 6:02d}-{(i % 28) + 1:02d}",
            "open": round(close * (1 - half_range / 2), 4),
            "close": close,
            "high": round(close * (1 + half_range), 4),
            "low": round(close * (1 - half_range), 4),
            "volume": 1000000 + i * 1000,
            "amount": 12000000.0,
        })
    return out


def _flat_klines(n: int = 30, level: float = 20.0) -> List[dict]:
    return [{"date": f"2026-07-{(i % 28) + 1:02d}", "open": level, "close": level,
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
        self.index = {"point": 3200.0, "chg_pct": 0.85, "ma20": 3150.0, "ma20_above": True}
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

    # 整池全坏：不能因为都在黑名单里就放弃，仍要把所有节点都试一遍
    tr_all = _FlakyTransport(cfg, set(pool))
    tr_all.f._dead_hosts = set(pool)
    try:
        tr_all.get_json(url, {"pn": 1}, host_pool="cdn_hosts_quote")
        t.ok("全坏时抛错", False)
    except Exception:
        t.ok("全坏时抛错", True)
    t.eq("全坏也把每个节点都试到", len(set(tr_all.hits)), len(pool))


def check_sentiment(t: Suite, cfg: Config, mk: FakeMarket) -> dict:
    t.head("道 · 情绪评分（§4.2 表逐项复算）")
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

    # 杂毛：后 50% -25、市值 -10、均线 -10、板块排名 +15
    q2 = mk.get_quote("600999")
    sec2 = mk.sector_context(q2)
    ind2 = mk.get_indicators("600999", q2["price"])
    idn2 = ident_mod.judge(q2, sec2, ind2, cfg)
    t.eq("杂毛身份分", idn2["score"], 50 - 25 + 15 - 10 - 10, tol=0.05)
    t.eq("杂毛层级", idn2["tier"], ident_mod.TIER_ZAMAO)
    t.ok("杂毛 flags ≥2", len(idn2["flags"]) >= 2, f"flags={idn2['flags']}")

    # §6.3 杂毛预警：门槛 6 → 7
    th_leader = preflight.effective_threshold(idn, None, False, False, cfg)
    th_zamao = preflight.effective_threshold(idn2, None, False, False, cfg)
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
    t.ok("R:R ≥ 3", lv["odds"] >= 3.0 - 1e-9 and lv["odds_ok"], f"odds={lv['odds']}")
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

    t.eq("①板块强度（排名1≤8 且涨停2≥2，内前10% +1 封顶2）", by_no[1]["score"], 2)
    t.eq("②大盘趋势（上证涨 + MA20 上）", by_no[2]["score"], 1)
    t.eq("③消息面（有催化）", by_no[3]["score"], 1)
    t.eq("④市值区间（120亿 ∈ 50~300）", by_no[4]["score"], 2)
    t.eq("⑤量价结构（放量上涨 + 多头，无扣分）", by_no[5]["score"], 2)
    t.eq("⑥止损结构（≤8% 且 /ATR≤2.5 且 R:R≥3）", by_no[6]["score"], 1)
    t.eq("共振总分", sc["total"], 9)
    t.eq("满分基准", sc["max"], 9)
    t.ok("无量价扣分项", not sc["penalties"], f"penalties={sc['penalties']}")

    # 扣分项：换手 >20% 应扣 1 分
    q_hi = dict(q, turnover=22.0)
    sc2 = preflight.score_nine(q_hi, ind, sec, sent, lv, has_news=True, cfg=cfg)
    t.eq("换手 22% 触发量价扣分", sc2["total"], 8)
    return sc


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
    r = veto_mod.check(base, dict(ind, bias_ma20=22.0), idn, 0.60, cfg)
    t.ok("龙头乖离 22% >20% → 软否决", "bias_ma20" in names(r, "soft"))
    r = veto_mod.check(base, dict(ind, bias_ma20=16.0),
                       {"tier": ident_mod.TIER_FOLLOW, "score": 50}, 0.60, cfg)
    t.ok("普通乖离 16% >15% → 软否决", "bias_ma20" in names(r, "soft"))
    r = veto_mod.check(base, ind, idn, 0.96, cfg)
    t.ok("分时 96% ≥95% → 硬否决", "intraday_hard" in names(r, "hard"))
    r = veto_mod.check(base, ind, {"tier": ident_mod.TIER_FOLLOW, "score": 50}, 0.80, cfg)
    t.ok("普通分时 80% >75% → 软否决", "intraday_high" in names(r, "soft"))
    r = veto_mod.check(base, ind, idn, 0.80, cfg)
    t.ok("龙头分时 80% ≤85% → 放行", not r["has_veto"])

    # 20cm 板阈值等比放大：创业板涨停区 = 9.5×2 = 19%
    gem = _quote("300123", "创业测试", 30.0, 15.0, HOT_NAME)
    r = veto_mod.check(gem, ind, idn, 0.60, cfg)
    t.ok("创业板涨 15% 未入涨停区（阈值×2）", "limit_up" not in names(r, "hard"))
    r = veto_mod.check(dict(gem, chg_pct=19.5), ind, idn, 0.60, cfg)
    t.ok("创业板涨 19.5% ≥19% → 硬否决", "limit_up" in names(r, "hard"))

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

    # MA20 下方 → 禁止新开评估
    below = dict(sent, ma20_above=False)
    g = gates.check_session_start(below, cfg, force=False, require_window=False)
    t.ok("上证 MA20 下方 → 禁止新开", any(b["rule"] == "上证MA20" for b in g.blocks))

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


def check_seed(t: Suite, cfg: Config, mk: FakeMarket, sent: dict) -> dict:
    t.head("扫描 · 种子四步流（§9）")
    sc = screener_mod.Screener(cfg, mk)
    step1 = sc.rank_sectors()
    t.ok("第1步：板块池非空", bool(step1["top"]),
         f"入池 {len(step1['top'])} 个，合格 {len(step1.get('qualified') or [])} 个")
    top_names = [s["name"] for s in step1["top"]]
    t.ok("极热板块入池", HOT_NAME in top_names, f"top={top_names[:3]}")

    res = sc.seed_scan(sent=sent, include_eve=True, write_trace=True)
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
    return res


def check_menu(t: Suite, cfg: Config) -> None:
    """菜单：默认只印四条建议，展开视图必须不多不少地盖住 20 项。

    分组表是手工维护的，日后加一个菜单项很容易忘了归组——那个功能就从
    界面上消失了，而且不报错。这里把两边对齐当成硬约束。
    """
    import datetime as _dt

    from . import cli
    from .timing import Timing

    t.head("菜单 · 分组与时段建议")

    keys = [k for k, _, _ in cli.MENU]
    grouped = [k for _, ks in cli.MENU_GROUPS for k in ks]
    t.eq("分组总数等于菜单项数", len(grouped), len(keys))
    t.ok("分组无重复", len(set(grouped)) == len(grouped))
    t.ok("分组无遗漏", set(grouped) == set(keys),
         f"缺 {sorted(set(keys) - set(grouped))} 多 {sorted(set(grouped) - set(keys))}")

    # 时段→建议：每个时段都得有东西可做，且不超过四条（否则就又回到平铺）。
    real_now = utils.now
    try:
        for stamp in ("2026-08-03 09:00", "2026-08-03 10:05", "2026-08-03 10:40",
                      "2026-08-03 12:17", "2026-08-03 14:10", "2026-08-03 14:50",
                      "2026-08-03 16:30", "2026-08-02 16:30"):
            fake = _dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
            utils.now = lambda when=None, _f=fake: _f
            ks = cli.suggest_keys(Timing(cfg))
            t.ok(f"{stamp} 建议 1-4 条且均合法",
                 1 <= len(ks) <= 4 and len(set(ks)) == len(ks) and all(k in keys for k in ks),
                 f"got={ks}")

        # 非交易日不能推荐新开：门禁必定拦回，推了就是领着人撞墙。
        holiday = _dt.datetime(2026, 8, 2, 16, 30)
        utils.now = lambda when=None, _f=holiday: _f
        t.ok("非交易日不推荐准入评估", "3" not in cli.suggest_keys(Timing(cfg)))

        # 买入窗口必须推荐准入评估：一天就这 45 分钟能新开。
        window = _dt.datetime(2026, 8, 3, 14, 10)
        utils.now = lambda when=None, _f=window: _f
        t.ok("买入窗口推荐准入评估", "3" in cli.suggest_keys(Timing(cfg)))
    finally:
        utils.now = real_now


def check_end_to_end(t: Suite, cfg: Config, mk: FakeMarket, sent: dict) -> None:
    t.head("主流程 · run_once 端到端（§14 验收 1）")
    from . import runner
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


def check_onboarding(t: Suite, home: str) -> None:
    """首次启动向导：能落盘、能校验、且第二次不再默默弹出。

    向导只跑一次，跑错了没人会发现：标记没打上就每次启动都问一遍，
    标记打了但值没写进去就是默默沿用默认值。两边都得验。
    """
    from . import onboarding
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

    # 跳过：不改任何值，但不再默默重弹
    cfg3 = Config({"paths": {"data_dir": "wizard_data3"}},
                  path=os.path.join(home, "wizard_config3.json"))
    res3 = onboarding.run_wizard(cfg=cfg3, io=IO(answers={"wizard_mode": "s"},
                                                interactive=False, quiet=True))
    t.eq("跳过不写入参数", (res3.get("mode"), res3.get("saved")), ("skip", False))
    t.ok("跳过仍打标记（不再纠缠）", not onboarding.is_first_run(cfg3))


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
        check_ztpool_fallback(t, c)
        check_host_failover(t, c)
        sent = check_sentiment(t, c, mk)
        idn = check_identity(t, c, mk)
        lv = check_levels(t, c, mk)
        check_scoring(t, c, mk, sent, lv)
        check_veto(t, c, mk, idn)
        check_position(t, c)
        check_gates(t, c, mk, sent)
        check_seed(t, c, mk, sent)
        check_menu(t, c)
        check_onboarding(t, tmp)
        check_end_to_end(t, c, mk, sent)
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
