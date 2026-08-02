"""术+法 — 系统化选股：种子扫描四步流（14:30 核心流程）。

第1步 板块综合排序（热度分×0.65 + 温和票结构分×0.35，影子池 +18）
第2步 三档涨幅窗筛选（严格 / 热点降级 / 强势跟涨，含降级逻辑与硬过滤）
第3步 VETO 过滤（软否决→观察轨，硬否决→REJECT）
第4步 预审快照 + 三档输出（可买 / 待启动观察 / 近失）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import followthrough, identity as ident_mod, preflight, seed_trace, utils
from . import veto as veto_mod
from . import watch_pool
from .config_store import Config, load_config
from .data import Market, count_limit_ups
from .sentiment import get_sentiment

TIER_STRICT = "严格档"
TIER_RELAXED = "热点降级档"
TIER_MOMENTUM = "强势跟涨档"
TIER_SPROUT = "萌芽窗口"
TIER_EVE = "前夕观察"

VERDICT_TRADEABLE = "HAS_TRADEABLE"
VERDICT_PENDING = "PENDING"
VERDICT_EMPTY = "EMPTY"


# ------------------------------------------------------------------ 档位参数

def tier_params(tier: str, cfg: Config) -> dict:
    c = lambda k, d=None: cfg.get(f"seed.{k}", d)
    if tier == TIER_STRICT:
        return {"name": tier, "min_chg": float(c("strict_min_chg", 3.0)), "max_chg": float(c("strict_max_chg", 5.5)),
                "min_identity": float(c("strict_min_identity", 70)), "min_pick": float(c("strict_min_pick", 60)),
                "rank_pct": float(c("strict_rank_pct", 0.35)), "min_turnover": float(c("strict_min_turnover", 2.0)),
                "cap_max": float(c("cap_max", 300.0)), "note": "温和突破，首选"}
    if tier == TIER_RELAXED:
        return {"name": tier, "min_chg": float(c("relaxed_min_chg", 3.0)), "max_chg": float(c("relaxed_max_chg", 7.5)),
                "min_identity": float(c("relaxed_min_identity", 65)), "min_pick": float(c("relaxed_min_pick", 62)),
                "rank_pct": float(c("relaxed_rank_pct", 0.40)), "min_turnover": float(c("relaxed_min_turnover", 3.0)),
                "cap_max": float(c("relaxed_cap_max", 300.0)), "note": "相对跟涨，仍拒杂毛"}
    if tier == TIER_MOMENTUM:
        return {"name": tier, "min_chg": float(c("momentum_min_chg", 5.0)),
                "max_chg": float(c("momentum_max_chg", 8.8)),
                "min_identity": float(c("momentum_min_identity", 68)),
                "min_identity_hot": float(c("momentum_min_identity_hot", 62)),
                "min_pick": float(c("momentum_min_pick", 60)),
                "rank_pct": float(c("momentum_rank_pct", 0.48)), "min_turnover": float(c("strict_min_turnover", 2.0)),
                "cap_max": float(c("cap_max", 300.0)), "note": "前排 momentum"}
    if tier == TIER_SPROUT:
        return {"name": tier, "min_chg": float(c("strict_min_chg", 3.0)), "max_chg": float(c("strict_max_chg", 5.5)),
                "min_identity": float(c("relaxed_min_identity", 65)), "min_pick": float(c("strict_min_pick", 60)) - 5,
                "rank_pct": float(c("relaxed_rank_pct", 0.40)), "min_turnover": float(c("strict_min_turnover", 2.0)),
                "cap_max": float(c("cap_max", 300.0)), "note": "萌芽窗口（严格窗内非涨停前排）"}
    return {"name": TIER_EVE, "min_chg": float(c("eve_min_chg", 1.0)), "max_chg": float(c("eve_max_chg", 3.0)),
            "min_identity": 0.0, "min_pick": 0.0, "rank_pct": float(c("relaxed_rank_pct", 0.40)),
            "min_turnover": float(c("front_row_min_turnover", 1.5)), "cap_max": float(c("cap_max", 300.0)),
            "note": "前夕观察（仅观察，永不写计划）"}


# ------------------------------------------------------------------ 系统分

def pick_score(member: dict, sector: dict, cfg: Config) -> dict:
    """系统分（0~100）：板块位置 35 + 板块内位置 25 + 涨幅贴合 20 + 换手 10 + 市值 10。"""
    parts: Dict[str, float] = {}
    s_rank = sector.get("rank") or 99
    parts["板块位置"] = utils.clamp(35.0 * (1.0 - (s_rank - 1) / 30.0), 0.0, 35.0)

    rank_pct = member.get("rank_pct")
    parts["板块内位置"] = utils.clamp(25.0 * (1.0 - (rank_pct if rank_pct is not None else 0.6)), 0.0, 25.0)

    lo = float(cfg.get("seed.strict_min_chg", 3.0))
    hi = float(cfg.get("seed.strict_max_chg", 5.5))
    chg = member.get("chg")
    if chg is None:
        parts["涨幅贴合"] = 0.0
    elif lo <= chg <= hi:
        parts["涨幅贴合"] = 20.0
    else:
        over = (chg - hi) if chg > hi else (lo - chg)
        parts["涨幅贴合"] = utils.clamp(20.0 - over * 5.0, 0.0, 20.0)

    to = member.get("turnover")
    if to is None:
        parts["换手"] = 5.0
    elif 3.0 <= to <= 10.0:
        parts["换手"] = 10.0
    elif 2.0 <= to <= 15.0:
        parts["换手"] = 6.0
    else:
        parts["换手"] = 3.0

    cap = member.get("cap_yi")
    if cap is None:
        parts["市值"] = 5.0
    elif 80.0 <= cap <= 200.0:
        parts["市值"] = 10.0
    elif 50.0 <= cap <= 300.0:
        parts["市值"] = 8.0
    else:
        parts["市值"] = 4.0

    total = sum(parts.values())
    return {"score": round(total, 1), "parts": {k: round(v, 1) for k, v in parts.items()}}


# ------------------------------------------------------------------ 影子池

def load_shadow(cfg: Config) -> dict:
    return utils.read_json(cfg.data_file("shadow_pool_file"), default=None) or {"date": None, "sectors": []}


def save_shadow(cfg: Config, sectors: List[dict]) -> str:
    return utils.write_json(cfg.data_file("shadow_pool_file"), {
        "date": utils.today_str(),
        "sectors": [{"bk": s["bk"], "name": s["name"], "score": s["total_score"]} for s in sectors],
    })


class Screener:
    """种子扫描器。"""

    def __init__(self, cfg: Optional[Config] = None, market: Optional[Market] = None):
        self.cfg = cfg or load_config()
        self.mk = market or Market(self.cfg)

    # ============================================================== 第 1 步
    def rank_sectors(self, tracer: Optional[seed_trace.Tracer] = None) -> dict:
        cfg = self.cfg
        c = lambda k, d=None: cfg.get(f"seed.{k}", d)
        topn = int(c("sector_scan_topn", 30))
        sectors = self.mk.get_sector_ranking()[:topn]
        shadow = load_shadow(cfg)
        shadow_names = {s.get("bk") for s in shadow.get("sectors", [])}
        scored: List[dict] = []

        for s in sectors:
            try:
                members = self.mk.get_sector_members(s["bk"])
            except Exception as exc:
                if tracer:
                    tracer.add_sector(s["name"], "成分股拉取失败", str(exc), bk=s["bk"])
                continue
            if not members:
                if tracer:
                    tracer.add_sector(s["name"], "成分股为空", bk=s["bk"])
                continue
            total_n = len(members)
            limit_ups = count_limit_ups(members)
            ups = sum(1 for m in members if (m.get("chg") or 0) > 0)
            mild = [m for m in members
                    if m.get("chg") is not None and float(c("mild_chg_low", 3.0)) <= m["chg"] <= float(c("mild_chg_high", 5.5))
                    and m.get("cap_yi") is not None
                    and float(c("mild_cap_low", 50.0)) <= m["cap_yi"] <= float(c("mild_cap_high", 300.0))
                    and m["chg"] < utils.limit_up_pct(m.get("code", ""), m.get("name", "")) - 0.2]
            mild_ratio = len(mild) / total_n if total_n else 0.0

            rank_score = max(0.0, float(c("rank_score_base", 40.0)) - s["rank"] * float(c("rank_score_step", 3.0)))
            zt_score = min(limit_ups / float(c("limit_up_score_div", 5.0)) * float(c("limit_up_score_cap", 35.0)),
                           float(c("limit_up_score_cap", 35.0)))
            chg_score = (float(c("chg_score_value", 25.0))
                         if float(c("chg_score_low", 2.0)) <= (s.get("chg") or 0) <= float(c("chg_score_high", 8.0))
                         else 0.0)
            heat = rank_score + zt_score + chg_score
            mild_score = min(mild_ratio / float(c("mild_target_ratio", 0.20)) * 100.0, 100.0)
            total_score = heat * float(c("heat_weight", 0.65)) + mild_score * float(c("mild_weight", 0.35))

            shadow_bonus = float(c("shadow_bonus", 18.0)) if s["bk"] in shadow_names else 0.0
            total_score += shadow_bonus

            entry = {
                **s, "members": members, "member_total": total_n, "limit_up_count": limit_ups,
                "up_ratio": ups / total_n if total_n else None,
                "mild_n": len(mild), "mild_ratio": round(mild_ratio, 4),
                "heat_score": round(heat, 1), "mild_score": round(mild_score, 1),
                "shadow_bonus": shadow_bonus, "total_score": round(total_score, 1),
                "has_mild": bool(mild),
            }
            scored.append(entry)

        # 板块硬门槛：排名 ≤8 且 涨停 ≥2 家（或 涨停1家 + 综合分 ≥60）
        min_rank = int(cfg.s("seed_min_sector_rank", 8))
        min_zt = int(cfg.s("seed_min_sector_limit_up", 2))
        relax_score = float(c("sector_relax_score", 60.0))
        qualified = []
        for e in scored:
            ok_hard = e["rank"] <= min_rank and e["limit_up_count"] >= min_zt
            ok_relax = e["limit_up_count"] >= 1 and e["total_score"] >= relax_score
            if ok_hard or ok_relax:
                e["gate"] = "硬门槛" if ok_hard else f"放宽（涨停1家+综合分≥{relax_score:.0f}）"
                qualified.append(e)
            elif tracer:
                tracer.add_sector(e["name"], "板块硬门槛不足",
                                  f"排名 {e['rank']}（需≤{min_rank}）涨停 {e['limit_up_count']} 家"
                                  f"（需≥{min_zt}）综合分 {e['total_score']}", bk=e["bk"])

        qualified.sort(key=lambda x: -x["total_score"])
        want = int(cfg.s("seed_top_sectors", 3))
        top = qualified[:want]

        # 多元化：若 TOP 全无温和票，则替换末位为最佳有温和票板块
        if top and not any(t["has_mild"] for t in top) and cfg.get("seed.diversify_replace_last", True):
            cand = next((e for e in qualified[want:] if e["has_mild"]), None)
            if cand:
                dropped = top[-1]
                top = top[:-1] + [cand]
                if tracer:
                    tracer.note(f"多元化替换：{dropped['name']}（无温和票）→ {cand['name']}"
                                f"（温和票 {cand['mild_n']} 只）")

        # 影子池：本次近榜板块留给次日 +18
        near = int(c("shadow_near_rank", 6))
        save_shadow(cfg, qualified[:near])

        max_chg = max([t.get("chg") or 0 for t in top], default=0.0)
        if tracer:
            tracer.note(f"板块综合排序：候选 {len(scored)} → 达标 {len(qualified)} → TOP{len(top)}"
                        f"（最强涨幅 {max_chg:.2f}%）")
        return {"top": top, "qualified": qualified, "scored": scored, "max_sector_chg": max_chg}

    # ============================================================== 第 2 步
    def screen_tier(self, sectors: List[dict], tier: str,
                    tracer: Optional[seed_trace.Tracer] = None) -> List[dict]:
        """单档涨幅窗筛选（硬过滤 + 身份/系统分门槛）。"""
        cfg = self.cfg
        p = tier_params(tier, cfg)
        out: List[dict] = []
        cap_min = float(cfg.get("seed.cap_min", 50.0))
        to_max = float(cfg.get("seed.turnover_max", 20.0))
        front_k = int(cfg.get("seed.front_row_topk", 3))
        front_to = float(cfg.get("seed.front_row_min_turnover", 1.5))
        hot_chg = float(cfg.s("seed_hot_sector_chg", 6.0))

        for sec in sectors:
            members = sec.get("members") or []
            total_n = len(members)
            hot = (sec.get("chg") or 0) >= hot_chg
            front_sector = (sec.get("rank") or 99) <= int(cfg.get("identity.hot_front_sector_rank", 8))
            for m in members:
                code, name, chg = m.get("code"), m.get("name") or "", m.get("chg")
                rank_pct = (m.get("rank") / total_n) if (m.get("rank") and total_n) else None
                m = {**m, "rank_pct": rank_pct}
                trace = (lambda reason, detail="": tracer.add(
                    seed_trace.STEP_WINDOW, code, name, reason, detail, tier=tier,
                    sector=sec.get("name"), chg=chg) if tracer else None)

                if utils.is_st(name):
                    trace("ST 过滤", name)
                    continue
                if not veto_mod.board_allowed(code, cfg):
                    trace("板块无权限", veto_mod.BOARD_NAMES.get(utils.board_of(code), "?"))
                    continue
                near = float(cfg.s("veto_near_limit_pct", 9.0)) * (utils.limit_up_pct(code, name) / 10.0)
                if chg is not None and chg >= near:
                    trace("涨停/接近涨停", f"涨幅 {chg:.2f}% ≥ {near:.2f}%")
                    continue
                if chg is None or not (p["min_chg"] <= chg <= p["max_chg"]):
                    trace("涨幅不在窗口", f"涨幅 {utils.pct(chg)} 不在 {p['min_chg']}~{p['max_chg']}%")
                    continue
                cap = m.get("cap_yi")
                if cap is None or cap < cap_min or cap > p["cap_max"]:
                    trace("市值不合格", f"{utils.num(cap)}亿 需 {cap_min:.0f}~{p['cap_max']:.0f}亿")
                    continue
                to = m.get("turnover")
                min_to = p["min_turnover"]
                if front_sector and (m.get("rank") or 99) <= front_k:
                    min_to = min(min_to, front_to)
                if to is None or to < min_to or to > to_max:
                    trace("换手不合格", f"{utils.num(to)}% 需 {min_to}~{to_max}%")
                    continue
                if rank_pct is None or rank_pct > p["rank_pct"]:
                    trace("板块内排名靠后", f"前 {rank_pct:.0%} 需 ≤{p['rank_pct']:.0%}"
                          if rank_pct is not None else "排名未知")
                    continue

                sec_ctx = {**{k: v for k, v in sec.items() if k != "members"},
                           "stock_rank": m.get("rank"), "stock_rank_pct": rank_pct,
                           "member_total": total_n, "found": True}
                pseudo_quote = {"code": code, "name": name, "chg_pct": chg, "cap_yi": cap,
                                "turnover": to, "is_st": utils.is_st(name),
                                "limit_up_pct": utils.limit_up_pct(code, name)}
                idn = ident_mod.judge(pseudo_quote, sec_ctx, None, cfg)
                min_id = p["min_identity"]
                if tier == TIER_MOMENTUM and hot:
                    min_id = p.get("min_identity_hot", min_id)
                elif hot and tier == TIER_RELAXED:
                    min_id = min(min_id, float(cfg.get("seed.hot_identity_relax", 62)))
                if ident_mod.is_zamao(idn):
                    trace("杂毛过滤", f"身份分 {idn['score']}（{idn['tier']}）标记 {len(idn['flags'])} 项")
                    continue
                if idn["score"] < min_id:
                    trace("身份分不足", f"身份分 {idn['score']} < {min_id:.0f}")
                    continue
                ps = pick_score(m, sec_ctx, cfg)
                if ps["score"] < p["min_pick"]:
                    trace("系统分不足", f"系统分 {ps['score']} < {p['min_pick']:.0f}（{ps['parts']}）")
                    continue

                out.append({
                    "code": code, "name": name, "chg": chg, "turnover": to, "cap_yi": cap,
                    "rank": m.get("rank"), "rank_pct": rank_pct,
                    "sector": sec_ctx, "sector_name": sec.get("name"), "sector_rank": sec.get("rank"),
                    "sector_chg": sec.get("chg"), "identity": idn, "pick": ps,
                    "tier": tier, "hot_sector": hot,
                })
        out.sort(key=lambda x: (-x["identity"]["score"], -x["pick"]["score"]))
        if tracer:
            tracer.note(f"{tier}：初筛通过 {len(out)} 只")
        return out

    def screen_with_downgrade(self, sectors: List[dict], max_sector_chg: float,
                              tracer: Optional[seed_trace.Tracer] = None) -> Tuple[List[dict], str, List[str]]:
        """三档降级逻辑：普涨日跳过严格档；逐档降级；三档全空则扫萌芽窗口。"""
        cfg = self.cfg
        notes: List[str] = []
        hot_chg = float(cfg.s("seed_hot_sector_chg", 6.0))
        order = [TIER_STRICT, TIER_RELAXED, TIER_MOMENTUM]
        if max_sector_chg >= hot_chg:
            order = [TIER_RELAXED, TIER_MOMENTUM]
            notes.append(f"板块最强涨幅 {max_sector_chg:.2f}% ≥{hot_chg:.0f}% → 跳过严格档（普涨日严格窗必空）")
        for tier in order:
            cands = self.screen_tier(sectors, tier, tracer)
            if cands:
                notes.append(f"启用 {tier}，候选 {len(cands)} 只")
                return cands, tier, notes
            notes.append(f"{tier} 0 只 → 降级")
        if cfg.get("seed.sprout_scan_enabled", True):
            cands = self.screen_tier(sectors, TIER_SPROUT, tracer)
            if cands:
                notes.append(f"三档全空 → 萌芽窗口扫描，候选 {len(cands)} 只")
                return cands, TIER_SPROUT, notes
        notes.append("三档 + 萌芽窗口全空 → 今日无种子")
        return [], TIER_STRICT, notes

    # ============================================================== 第 3 步
    def veto_filter(self, candidates: List[dict], sent: Optional[dict],
                    tracer: Optional[seed_trace.Tracer] = None) -> dict:
        """逐只拉行情做 VETO：软否决→观察轨，硬否决→REJECT。"""
        cfg = self.cfg
        cap = int(cfg.get("seed.candidate_fetch_cap", 30))
        passed: List[dict] = []
        soft: List[dict] = []
        for cand in candidates[:cap]:
            try:
                ev = preflight.evaluate(
                    cand["code"], self.mk, cfg, sent=sent, sector=cand["sector"],
                    seed_leader_relax=True)
            except Exception as exc:
                if tracer:
                    tracer.add(seed_trace.STEP_VETO, cand["code"], cand["name"], "行情异常", str(exc))
                continue
            ev["tier_label"] = cand["tier"]
            ev["pick"] = cand["pick"]
            vt = ev.get("veto") or {}
            if vt.get("rejected"):
                if tracer:
                    tracer.add(seed_trace.STEP_VETO, cand["code"], cand["name"], "硬否决",
                               "；".join(i["label"] for i in vt["hard"]), tier=cand["tier"])
                continue
            if vt.get("soft"):
                ev["track"] = watch_pool.TRACK_WATCH
                ev["triggers"] = followthrough.trigger_conditions(ev, cfg)
                soft.append(ev)
                if tracer:
                    tracer.add(seed_trace.STEP_VETO, cand["code"], cand["name"], "软否决→观察轨",
                               "；".join(i["label"] for i in vt["soft"]), tier=cand["tier"])
                continue
            passed.append(ev)
        return {"passed": passed, "soft": soft}

    # ============================================================== 第 4 步
    def preflight_outputs(self, evaluations: List[dict], tracer: Optional[seed_trace.Tracer] = None) -> dict:
        """预审三档输出：可买 / 待启动观察 / 近失。"""
        cfg = self.cfg
        max_out = int(cfg.s("seed_max_output", 2))
        max_watch = int(cfg.get("seed.max_watch_output", 3))
        max_near = int(cfg.get("seed.max_near_miss_output", 6))
        gap_watch = int(cfg.get("seed.near_miss_gap", 1))

        buyable, watch, near = [], [], []
        for ev in evaluations:
            gap = ev.get("gap") if ev.get("gap") is not None else 99
            ft = followthrough.evaluate((ev.get("stage") or {}).get("stage"),
                                        ev.get("tier_label"), ev.get("track"), cfg)
            ev["followthrough"] = ft
            if ev.get("verdict") == preflight.VERDICT_PASS:
                buyable.append(ev)
            elif gap <= gap_watch:
                ev["track"] = watch_pool.TRACK_PENDING
                ev["triggers"] = followthrough.trigger_conditions(ev, cfg)
                watch.append(ev)
                if tracer:
                    tracer.add(seed_trace.STEP_PREFLIGHT, ev["code"], ev["name"], "差1分→启动待定轨",
                               f"共振 {ev.get('total_score')}/{ev.get('pass_threshold')}")
            else:
                near.append(ev)
                if tracer:
                    tracer.add(seed_trace.STEP_PREFLIGHT, ev["code"], ev["name"], "近失（只复盘）",
                               f"共振 {ev.get('total_score')}/{ev.get('pass_threshold')}，"
                               + "；".join(ev.get("reasons", [])))

        # 排序：共振分 → 跟涨经验 → 身份分
        keyf = lambda e: (-(e.get("total_score") or 0),
                          -((e.get("followthrough") or {}).get("score") or 0),
                          -((e.get("identity") or {}).get("score") or 0))
        buyable.sort(key=keyf)
        watch.sort(key=keyf)
        near.sort(key=keyf)
        return {"buyable": buyable[:max_out], "watch": watch[:max_watch], "near_miss": near[:max_near],
                "buyable_all": buyable, "dropped_buyable": buyable[max_out:]}

    # ============================================================== 前夕观察
    def eve_scan(self, sectors: List[dict], sent: Optional[dict],
                 tracer: Optional[seed_trace.Tracer] = None) -> List[dict]:
        """前夕观察（1%~3% 涨幅窗）：仅观察/报告，永不写计划。"""
        cfg = self.cfg
        cands = self.screen_tier(sectors, TIER_EVE, tracer)
        out: List[dict] = []
        limit = int(cfg.get("seed.max_watch_output", 3))
        for cand in cands[:limit * 3]:
            try:
                ev = preflight.evaluate(cand["code"], self.mk, cfg, sent=sent,
                                               sector=cand["sector"])
            except Exception:
                continue
            if (ev.get("veto") or {}).get("rejected"):
                continue
            if (ev.get("stage") or {}).get("overheat"):
                continue
            ev["tier_label"] = TIER_EVE
            ev["track"] = watch_pool.TRACK_EVE
            lo = float(cfg.get("seed.strict_min_chg", 3.0))
            hi = float(cfg.get("seed.strict_max_chg", 5.5))
            intr = float(cfg.get("seed.eve_trigger_intraday", 0.75))
            ev["triggers"] = [f"涨入严格窗 {lo}~{hi}%", f"分时回落至 ≤{intr:.0%} 后再预审"]
            out.append(ev)
            if len(out) >= limit:
                break
        return out

    # ============================================================== 主流程
    def seed_scan(self, sent: Optional[dict] = None, include_eve: bool = True,
                  write_trace: bool = True) -> dict:
        """种子扫描四步流总入口。"""
        cfg = self.cfg
        tracer = seed_trace.Tracer(cfg)
        sent = sent if sent is not None else get_sentiment(self.mk, cfg)

        step1 = self.rank_sectors(tracer)
        sectors = step1["top"]
        result: Dict[str, Any] = {
            "at": utils.now().strftime("%Y-%m-%d %H:%M"), "scan_id": tracer.scan_id,
            "sentiment": sent, "sectors": [{k: v for k, v in s.items() if k != "members"} for s in sectors],
            "sector_pool": [{k: v for k, v in s.items() if k != "members"} for s in step1["qualified"][:10]],
            "max_sector_chg": step1["max_sector_chg"],
            "tier": None, "buyable": [], "watch": [], "near_miss": [], "eve": [],
            "notes": [], "verdict": VERDICT_EMPTY,
        }
        if not sectors:
            result["notes"].append("无板块通过硬门槛（排名≤8 且涨停≥2家 / 涨停1家+综合分≥60）")
            tracer.note(result["notes"][-1])
            if write_trace:
                result["trace"] = tracer.flush()
            return result

        cands, tier, notes = self.screen_with_downgrade(sectors, step1["max_sector_chg"], tracer)
        result["tier"], result["notes"] = tier, notes

        vf = self.veto_filter(cands, sent, tracer)
        out = self.preflight_outputs(vf["passed"], tracer)
        watch_items = vf["soft"] + out["watch"]

        result["buyable"] = out["buyable"]
        result["watch"] = watch_items[:int(cfg.get("seed.max_watch_output", 3)) + len(vf["soft"])]
        result["near_miss"] = out["near_miss"]
        result["candidates_n"] = len(cands)
        result["veto_passed_n"] = len(vf["passed"])
        result["soft_n"] = len(vf["soft"])

        if include_eve:
            try:
                result["eve"] = self.eve_scan(sectors, sent, tracer)
            except Exception as exc:
                result["notes"].append(f"前夕观察扫描异常：{exc}")

        if result["buyable"]:
            result["verdict"] = VERDICT_TRADEABLE
        elif result["watch"] or result["eve"]:
            result["verdict"] = VERDICT_PENDING
        else:
            result["verdict"] = VERDICT_EMPTY

        if write_trace:
            result["trace"] = tracer.flush()
        return result
