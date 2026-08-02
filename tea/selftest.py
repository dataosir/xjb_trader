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
        sent = check_sentiment(t, c, mk)
        idn = check_identity(t, c, mk)
        lv = check_levels(t, c, mk)
        check_scoring(t, c, mk, sent, lv)
        check_veto(t, c, mk, idn)
        check_position(t, c)
        check_gates(t, c, mk, sent)
        check_seed(t, c, mk, sent)
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
