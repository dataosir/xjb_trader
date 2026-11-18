"""跟涨经验胜率 + 观察触发条件。

从历史种子记录（seed_records.jsonl）按 阶段×档位×轨道 聚合 T+1 胜率
（T+1 涨幅 ≥3% 计为赢），样本 ≥8 才有效；用于种子排序与半仓系数调整。

归因统计只吃 ``data_status = valid`` 的样本（见 ``record_status``）：2026-08 之前
落盘的结构没有 pass_threshold / scoring_dims / mode，参与归因只会让「过没过线」
无法复核。这些旧样本标 ``legacy`` 后仍保留行情回填值，但不进分子分母。

另把「按收盘卖出」的结果和「按止损/止盈挂单成交」的结果区分开（``exit_t*``）：
持有期涨幅为正不等于能赚到钱，同一条记录按 6% 止损挂单可能已经是 -6%。
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import math
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from tea.analysis import shadow_lab as shadow_lab_mod
from tea.config.config_store import Config, load_config
from tea.core import utils
from tea.data import Market
from tea.screening.scan_anchor import ANCHOR_PRIMARY, anchor_rank

KEY_SEP = "|"


def records_path(cfg: Optional[Config] = None) -> str:
    return (cfg or load_config()).data_file("seed_records_file")


def load_records(cfg: Optional[Config] = None) -> List[dict]:
    return utils.read_jsonl(records_path(cfg))


#: 结构性字段缺失（gate 门槛 / 通道标记）的样本视为无效：它们来自 2026-08 之前的
#: 落盘结构，没有 pass_threshold、scoring_dims、mode，参与归因只会让「过没过线」
#: 无法复核。数据本身留着（T+N 回填是真实行情），只是统计时不进分子分母。
STATUS_VALID = "valid"
STATUS_LEGACY = "legacy"

#: 回填算法版本。字段/口径改动时 +1：旧样本会在下一次 review 强制重算一次，
#: 版本一致且字段齐全的样本不再每天重抓 120 根 K 线。
BACKFILL_VER = 2

#: 扫描数据质量。degraded 的样本不进入胜率归因，避免「网络缺数」被当成选股结果。
QUALITY_OK = "ok"
QUALITY_DEGRADED = "degraded"

#: 各周期涨幅字段 → 对应的「收盘 > 阈值」成败标记。
_HORIZON_RESULTS = (("next_chg", "result_t1"), ("chg_t2", "result_t2"),
                    ("chg_t3", "result_t3"), ("chg_t5", "result_t5"))

#: 成交价模拟的两条挂单：跌破止损按止损价成交，盘中最高价触及止盈按止盈价成交。
#: 记录里只有收盘价，所以模拟出来的 ``low_t*`` / ``high_t*`` 是下限/上限——用它们
#: 判断「会不会被打出去」只会误判成更差，恰好是保守方向。
_EXIT_FIELDS = ("exit_t1", "exit_t2", "exit_t3", "exit_t5",
                "low_t1", "low_t2", "low_t3", "low_t5",
                "high_t1", "high_t2", "high_t3", "high_t5")


def record_status(r: dict) -> str:
    """样本是否可用于归因：只认显式 data_status，缺省视为 valid。

    不在读取时按结构字段推断：新增字段是渐进的，缺字段不等于样本失效；
    失效只由维护动作显式标记（见 docs/CHANGELOG「失效历史样本」）。
    """
    return str(r.get("data_status") or STATUS_VALID)


def record_quality(r: dict) -> str:
    """扫描数据质量：缺省 ok；degraded 表示当日关键行情/情绪数据有缺口。"""
    return str(r.get("data_quality") or QUALITY_OK)


def load_valid_records(cfg: Optional[Config] = None) -> List[dict]:
    """只取可归因的样本：data_status=valid 且扫描数据未降级。"""
    return [r for r in load_records(cfg)
            if record_status(r) == STATUS_VALID
            and record_quality(r) != QUALITY_DEGRADED]


def data_health(cfg: Optional[Config] = None) -> dict:
    """样本健康度：总条数、失效/降级条数、各周期已回填数、与口径不一致的条数。"""
    recs = load_records(cfg)
    legacy = [r for r in recs if record_status(r) == STATUS_LEGACY]
    valid = [r for r in recs if record_status(r) == STATUS_VALID]
    degraded = [r for r in valid if record_quality(r) == QUALITY_DEGRADED]
    out = {"total": len(recs), "valid": len(valid), "usable": len(valid) - len(degraded),
           "legacy": len(legacy), "degraded": len(degraded),
           "legacy_range": None, "pending": {}, "missing_result": {}}
    if legacy:
        dates = sorted(str(r.get("date")) for r in legacy)
        out["legacy_range"] = (dates[0], dates[-1])
    for field, flag in _HORIZON_RESULTS:
        out["pending"][field] = sum(1 for r in valid if r.get(field) is None)
        out["missing_result"][flag] = sum(
            1 for r in valid if r.get(field) is not None and r.get(flag) is None)
    out["pending_all"] = {f: sum(1 for r in recs if r.get(f) is None)
                          for f, _ in _HORIZON_RESULTS}
    return out


def _wilson_ci(up: int, n: int, z: float = 1.96) -> Optional[Tuple[float, float]]:
    """二项比例的 Wilson 95% 置信区间；n=0 返回 None。"""
    if n <= 0:
        return None
    p = up / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def horizon_winrates(cfg: Optional[Config] = None) -> Dict[str, dict]:
    """T+1/T+2/T+3/T+5 同口径（收盘 > 0）胜率对照，外加原 result（T+1 ≥3%）。

    result 是老的跟涨经验口径（+3% 才算赢），与归因用的「涨了没有」不是一回事，
    并排给出才能看出「是选错了票」还是「只是没到 +3%」。
    """
    cfg = cfg or load_config()
    pool = load_valid_records(cfg)
    out: Dict[str, dict] = {}
    for field, flag in _HORIZON_RESULTS:
        n = up = 0
        for r in pool:
            st = r.get(flag)
            if st not in ("win", "loss"):
                continue
            n += 1
            up += 1 if st == "win" else 0
        out[field] = {"n": n, "up": up, "rate": (up / n) if n else None,
                      "ci": _wilson_ci(up, n)}
    n = w = 0
    for r in pool:
        if r.get("result") not in ("win", "loss"):
            continue
        n += 1
        w += 1 if r["result"] == "win" else 0
    out["result_t1_pct3"] = {"n": n, "up": w, "rate": (w / n) if n else None,
                             "ci": _wilson_ci(w, n)}
    return out


def format_horizon_winrates(cfg: Optional[Config] = None) -> str:
    """收盘口径 T+1/T+2/T+3/T+5 一行摘要，附 95% 置信区间。"""
    h = horizon_winrates(cfg)
    labels = (("next_chg", "T+1"), ("chg_t2", "T+2"),
              ("chg_t3", "T+3"), ("chg_t5", "T+5"))
    parts = []
    for field, label in labels:
        s = h.get(field) or {}
        if not s.get("n"):
            parts.append(f"{label} —")
            continue
        ci = s.get("ci")
        ci_txt = f" [{ci[0]:.0%},{ci[1]:.0%}]" if ci else ""
        tail = "·样本不足" if s["n"] < 30 else ""
        parts.append(f"{label}>0 {s['rate']:.0%}{ci_txt}"
                     f"（{s['up']}/{s['n']}{tail}）")
    return "收盘口径（>0）：" + "｜".join(parts)


# 轨道优先级：同一天同一只票按「更接近可成交」的轨道保留。早盘先以观察/启动待定
# 落盘、午后升级为「可买」时，若仍按 (date, code) 只记首条，会把「可买」样本漏掉，
# 导致可买轨道的跟涨胜率被系统性低估。
_TRACK_PRIORITY = {
    "可买": 5,
    "启动待定轨": 4,
    "萌芽观察轨": 3,
    "观察轨": 2,
    "前夕观察轨": 1,
    "趋势轨": 0,
}


def _track_rank(track: Optional[str]) -> int:
    return _TRACK_PRIORITY.get(track, -1)


def _should_replace_record(old: dict, new_rec: dict, new_anchor: str) -> bool:
    """同日同票是否用新快照覆盖（保留已回填 T+N）。

    锚点 primary > manual > winrate；rule 通道优先于 winrate；同锚点内按轨道升级。
    primary 同轨道可刷新快照（漏扫重跑）。
    """
    old_mode = old.get("mode") or "rule"
    new_mode = new_rec.get("mode") or "rule"
    if old_mode == "rule" and new_mode == "winrate":
        return False
    if old_mode == "winrate" and new_mode == "rule":
        new_r = anchor_rank(new_anchor)
        old_r = anchor_rank(old.get("scan_anchor"))
        if new_r > old_r:
            return True
        return _track_rank(new_rec.get("track")) > _track_rank(old.get("track"))

    new_r = anchor_rank(new_anchor)
    old_r = anchor_rank(old.get("scan_anchor"))
    if new_r > old_r:
        return True
    if new_r < old_r:
        return False

    new_tr = _track_rank(new_rec.get("track"))
    old_tr = _track_rank(old.get("track"))
    if new_tr > old_tr:
        return True
    if new_tr < old_tr:
        return False
    return new_anchor == ANCHOR_PRIMARY


# ------------------------------------------------------------------ 影子对照标签（不驱动计划）

def compute_shadow_tag(stage: Optional[str], sector_rank: Any = None,
                       pick_sector_rank: Any = None) -> Optional[str]:
    """影子对照桶标签：萌芽 ∪（非突破 ∧ rank≤3）。

    只用于落盘对照 T+3>0，**不写计划、不改可买**。优先用入选板块
    ``pick_sector_rank``，缺则退回预审 ``sector_rank``。
    """
    tags: List[str] = []
    if stage == "萌芽":
        tags.append("萌芽")
    rank = pick_sector_rank if pick_sector_rank is not None else sector_rank
    try:
        rank_i = int(rank) if rank is not None else None
    except (TypeError, ValueError):
        rank_i = None
    if stage and stage != "突破" and rank_i is not None and rank_i <= 3:
        tags.append("前三非突破")
    return "|".join(tags) if tags else None


def resolve_shadow_tag(rec: dict) -> Optional[str]:
    """读已落盘标签；缺则按阶段/排名重算（兼容闸门前旧样本）。"""
    raw = rec.get("shadow_tag")
    if raw:
        return str(raw)
    return compute_shadow_tag(rec.get("stage"), rec.get("sector_rank"),
                              rec.get("pick_sector_rank"))


def record_seed(entries: List[dict], cfg: Optional[Config] = None,
                date: Optional[str] = None,
                scan_anchor: Optional[str] = None) -> dict:
    """落盘当日种子记录（供次日回填 T+1 结果），按 (date, code) 去重。

    seed-plan 一天可能跑多次，同一标的会被重复 append，导致 T+1 样本翻倍、胜率
    失真。这里以 (date, code) 去重，但同一天内同一标的的轨道可能升级（观察→启动
    待定→可买），只记首条会把「可买」漏掉——所以按轨道优先级升级保留更高者。

    幂等锚点 ``scan_anchor``（primary/manual/winrate）：14:30 主扫描可覆盖早盘
    manual 快照；manual 不能覆盖 primary；winrate 不覆盖 rule。
    返回 {"added": n, "skipped": m, "updated": k}。
    """
    cfg = cfg or load_config()
    d = date or utils.today_str()
    recs = load_records(cfg)
    by_key = {(r.get("date"), r.get("code")): i
              for i, r in enumerate(recs) if r.get("date") and r.get("code")}
    added = skipped = updated = 0
    changed = False
    for e in entries:
        code = e.get("code")
        if not code:
            skipped += 1
            continue
        key = (d, code)
        rec = {
            "date": d, "code": code, "name": e.get("name"),
            "stage": e.get("stage"), "tier": e.get("tier"), "track": e.get("track"),
            "chg_pct": e.get("chg_pct"), "total_score": e.get("total_score"),
            "pass_threshold": e.get("pass_threshold"),
            "core_score": e.get("core_score"), "core_threshold": e.get("core_threshold"),
            "identity_tier": e.get("identity_tier"), "identity_score": e.get("identity_score"),
            "sector_name": e.get("sector_name"), "sector_rank": e.get("sector_rank"),
            "sector_chg": e.get("sector_chg"),
            "scoring_dims": e.get("scoring_dims"),
            "market_score": e.get("market_score"), "market_cycle": e.get("market_cycle"),
            "market_stance": e.get("market_stance"), "market_ma20_above": e.get("market_ma20_above"),
            "market_idx_chg": e.get("market_idx_chg"),
            "bias_ma20": e.get("bias_ma20"),
            "bb_mid": e.get("bb_mid"),
            "bb_upper": e.get("bb_upper"),
            "bb_lower": e.get("bb_lower"),
            "bb_pct_b": e.get("bb_pct_b"),
            "bb_bandwidth": e.get("bb_bandwidth"),
            "atr_pct": e.get("atr_pct"),
            "vol_ratio": e.get("vol_ratio"), "turnover": e.get("turnover"),
            "intraday": e.get("intraday"),
            "ma_bull": e.get("ma_bull"), "above_ma20": e.get("above_ma20"),
            "amount_yi": e.get("amount_yi"), "cap_yi": e.get("cap_yi"),
            "rank_pct": e.get("rank_pct"), "odds": e.get("odds"),
            "sl_pct": e.get("sl_pct"), "tp_pct": e.get("tp_pct"),
            "veto_labels": e.get("veto_labels"),
            "lowbuy": bool(e.get("lowbuy")),
            "winrate_score": e.get("winrate_score"),
            "winrate_detail": e.get("winrate_detail"),
            "winrate_score_v2": e.get("winrate_score_v2"),
            "winrate_score_v2_detail": e.get("winrate_score_v2_detail"),
            "winrate_gate": e.get("winrate_gate"),
            "winrate_would_buy": e.get("winrate_would_buy"),
            "winrate_t3_prob": e.get("winrate_t3_prob"),
            "winrate_t3_detail": e.get("winrate_t3_detail"),
            "mode": e.get("mode", "rule"),
            "scan_at": e.get("scan_at"),
            "scan_anchor": scan_anchor or e.get("scan_anchor"),
            "pick_sector_bk": e.get("pick_sector_bk"),
            "pick_sector_name": e.get("pick_sector_name"),
            "pick_sector_rank": e.get("pick_sector_rank"),
            "shadow_tag": (e.get("shadow_tag")
                           or compute_shadow_tag(
                               e.get("stage"), e.get("sector_rank"),
                               e.get("pick_sector_rank"))),
            # 选票时的现价只是「盘中价」，收盘可能差几个点。回填会用记录日 K 线收盘
            # 按涨幅重推出真实选票价并标 basis=derived；这里保留盘中价只作无 K 线时的兜底。
            "pick_close": e.get("price"),
            "pick_price_basis": "recorded" if e.get("price") is not None else None,
            "close": e.get("price"), "next_chg": None, "result": None,
            "result_t1": None, "result_t2": None, "result_t3": None, "result_t5": None,
            "data_status": STATUS_VALID,
            "data_quality": e.get("data_quality") or QUALITY_OK,
            "chg_t2": None, "chg_t3": None, "chg_t5": None,
        }
        rec.update(shadow_lab_mod.compute_fields(rec, cfg))
        if key not in by_key:
            recs.append(rec)
            by_key[key] = len(recs) - 1
            added += 1
            changed = True
            continue
        idx = by_key[key]
        old = recs[idx]
        if _should_replace_record(old, rec, scan_anchor or ""):
            # 选票价只认第一次：主扫描升级轨道时价格会变，回填基准若跟着漂，
            # 「从选票当天收盘算起」就变成了「从最后一次扫描价算起」。
            rec["pick_close"] = old.get("pick_close") or old.get("close")
            rec["pick_price_basis"] = old.get("pick_price_basis")
            rec["backfill_ver"] = old.get("backfill_ver")
            rec["backfill_date"] = old.get("backfill_date")
            rec["backfill_note"] = old.get("backfill_note")
            rec["next_chg"] = old.get("next_chg")
            rec["result"] = old.get("result")
            for _, flag in _HORIZON_RESULTS:
                rec[flag] = old.get(flag)
            rec["chg_t2"] = old.get("chg_t2")
            rec["chg_t3"] = old.get("chg_t3")
            rec["chg_t5"] = old.get("chg_t5")
            # 同一天只要有一次扫描数据降级，样本就保留 degraded 标记；不因后续
            # 补扫把已经污染的样本“洗白”。
            if old.get("data_quality") == QUALITY_DEGRADED:
                rec["data_quality"] = QUALITY_DEGRADED
            for k in ("shadow_lab_sector", "shadow_lab_bad_combo",
                      "shadow_lab_wr_consensus", "shadow_lab_bbw",
                      "shadow_lab_quality", "shadow_lab_quality_detail",
                      "winrate_score_v2", "winrate_score_v2_detail"):
                if rec.get(k) is None:
                    rec[k] = old.get(k)
            recs[idx] = rec
            updated += 1
            changed = True
        else:
            skipped += 1
    if changed:
        save_records(recs, cfg)
    return {"added": added, "skipped": skipped, "updated": updated}


def _merge_newer_on_disk(records: List[dict], cfg: Optional[Config] = None) -> List[dict]:
    """写回前合入「别的进程刚写进去、自己内存里没有」的记录。

    形态：seed-plan 先读全量、落盘新样本；后台回填（另一进程）随后写回时用的是
    更早的快照，整体重写会把刚落的样本抹掉——11-10 的 001330 就这样丢过一次。
    以 (date, code) 为键，磁盘上有而内存里没有的整条补回；两边都有的以内存为准
    （当前进程刚回填过行情字段，更可信）。
    """
    try:
        on_disk = load_records(cfg)
    except Exception:
        return records
    have = {(r.get("date"), r.get("code")) for r in records}
    extra = [r for r in on_disk if (r.get("date"), r.get("code")) not in have]
    return records + extra if extra else records


def save_records(records: List[dict], cfg: Optional[Config] = None,
                 merge_disk: bool = True) -> str:
    """整体重写（回填 T+1 结果后使用，原子写）。

    默认写前先合入磁盘上的新记录；只有明确要「整体替换」的场景才关掉
    （例如自测里重置样本基线）。
    """
    cfg = cfg or load_config()
    if merge_disk:
        records = _merge_newer_on_disk(records, cfg)
    lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    return utils.atomic_write(records_path(cfg), lines + ("\n" if lines else ""))


def _scan_sector_chg_lookup(cfg: Config) -> Dict[Tuple[str, str], float]:
    """从同日主 scan_details 提取 (date, sector_name) -> 板块涨幅。"""
    out: Dict[Tuple[str, str], float] = {}
    pattern = os.path.join(cfg.data_dir(),
                           "scan_details_[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")
    for path in sorted(glob.glob(pattern)):
        data = utils.read_json(path, {}) or {}
        date = str(data.get("scan_date")
                   or os.path.basename(path)[len("scan_details_"):-len(".json")])
        for sec in data.get("sectors") or []:
            name, chg = sec.get("name"), sec.get("chg")
            if not name or chg is None:
                continue
            try:
                out[(date, str(name).strip())] = round(float(chg), 2)
            except (TypeError, ValueError):
                continue
    return out


def _backup_records(cfg: Config) -> Optional[str]:
    src = records_path(cfg)
    if not os.path.isfile(src):
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dst = f"{src}.backup.{stamp}"
    shutil.copy2(src, dst)
    return dst


def backfill_sector_chg(cfg: Optional[Config] = None, *,
                        dry_run: bool = False) -> dict:
    """用同日 ``scan_details`` 的板块涨幅补历史样本缺失的 ``sector_chg``。

    只补缺失值，已有值不覆盖；按（记录日, 板块名）精确匹配。主 scan_details
    通常只保留当日板块池，匹配不到的行保持 ``None``，不猜。写盘前自动备份。
    """
    cfg = cfg or load_config()
    recs = load_records(cfg)
    missing_before = sum(1 for r in recs if r.get("sector_chg") is None)
    lookup = _scan_sector_chg_lookup(cfg)
    updated = 0
    unmatched = 0
    sample: List[str] = []
    for r in recs:
        if r.get("sector_chg") is not None:
            continue
        name = str(r.get("sector_name") or r.get("pick_sector_name") or "").strip()
        chg = lookup.get((str(r.get("date") or ""), name)) if name else None
        if chg is None:
            unmatched += 1
            continue
        r["sector_chg"] = chg
        updated += 1
        if len(sample) < 5:
            sample.append(f"{r.get('date')} {r.get('code')} {name} {chg:+.2f}%")
    out = {
        "total": len(recs),
        "missing_before": missing_before,
        "existing": len(recs) - missing_before,
        "updated": updated,
        "unmatched": unmatched,
        "dry_run": dry_run,
        "backup": None,
        "sample": sample,
    }
    if updated and not dry_run:
        out["backup"] = _backup_records(cfg)
        save_records(recs, cfg)
    return out


def dedupe_records(cfg: Optional[Config] = None) -> dict:
    """历史跟涨样本按 (date, code) 去重（保留首条），返回 {before, after, removed}。

    去重只对新写入生效（见 record_seed），已落盘的历史重复需要这里一次性清理，
    否则 update_results / aggregate 仍会被历史重复污染跟涨胜率。原子写，安全。
    """
    cfg = cfg or load_config()
    recs = load_records(cfg)
    seen = set()
    kept: List[dict] = []
    for r in recs:
        key = (r.get("date"), r.get("code"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(r)
    removed = len(recs) - len(kept)
    if removed:
        save_records(kept, cfg)
    return {"before": len(recs), "after": len(kept), "removed": removed}


# ------------------------------------------------------------------ T+1 回填

def _derive_pick_price(day_close: Optional[float],
                       chg_pct: Optional[float]) -> Optional[float]:
    """由当日收盘与涨幅回推选票价（= 昨收）。老样本只存了扫描价，没有这一列。"""
    if not day_close:
        return None
    if chg_pct is None or float(chg_pct) <= -99.9:
        return float(day_close)
    return round(float(day_close) / (1 + float(chg_pct) / 100.0), 2)


def _simulate_exit(bars: List[dict], base: float,
                   sl_pct: Optional[float], tp_pct: Optional[float]) -> Optional[float]:
    """按「跌破止损 → 止损价成交；盘中触到止盈 → 止盈价成交；都没触发 → 期末收盘」模拟。

    同一根 K 线里止损与止盈都可能触到，无法从日线判断先后，按先止损处理（保守）。
    没落盘的 sl_pct / tp_pct 返回 None，不参与统计。
    """
    if not bars or not base or sl_pct is None:
        return None
    sl = base * (1 - float(sl_pct) / 100.0)
    tp = base * (1 + float(tp_pct) / 100.0) if tp_pct is not None else None
    last = base
    for k in bars:
        low, high = k.get("low"), k.get("high")
        if low is not None and float(low) <= sl:
            return round((sl - base) / base * 100.0, 2)
        if tp is not None and high is not None and float(high) >= tp:
            return round((tp - base) / base * 100.0, 2)
        if k.get("close"):
            last = float(k["close"])
    return round((last - base) / base * 100.0, 2)


def horizon_conditional(chg_lo: float = 5.5, chg_hi: float = 7.5,
                        cfg: Optional[Config] = None) -> dict:
    """「涨幅 X~Y% 这一档，再按板块强度/量比拆开，T+3 是否还是坑」。

    单调桶会把条件互相抵消：5.5~7.5% 追高桶整体只有 30% T+3>0，但其中板块强度 2
    的反而是 43%、板块强度 0 的是 0%——所以要拆开看才指导得了选股。
    """
    cfg = cfg or load_config()
    pool = [r for r in load_valid_records(cfg) if r.get("chg_t3") is not None]
    base = [r for r in pool if r.get("chg_pct") is not None
            and chg_lo <= float(r["chg_pct"]) < chg_hi]

    def _stats(rows: List[dict]) -> dict:
        if not rows:
            return {"n": 0, "up": 0, "rate": None, "avg": None}
        up = sum(1 for r in rows if float(r["chg_t3"]) > 0)
        return {"n": len(rows), "up": up, "rate": up / len(rows),
                "avg": sum(float(r["chg_t3"]) for r in rows) / len(rows)}

    out = {"range": (chg_lo, chg_hi), "all": _stats(base), "by_sector": {}, "by_vr": {}}
    for r in base:
        dims = {d.get("name"): d.get("score") for d in (r.get("scoring_dims") or [])}
        s1 = dims.get("板块强度")
        if s1 is not None:
            out["by_sector"].setdefault(f"板块强度{int(s1)}", []).append(r)
        vr = r.get("vol_ratio")
        if vr is not None:
            lab = "量比<1.2" if float(vr) < 1.2 else "量比1.2~3" if float(vr) < 3 else "量比≥3"
            out["by_vr"].setdefault(lab, []).append(r)
    out["by_sector"] = {k: _stats(v) for k, v in sorted(out["by_sector"].items())}
    out["by_vr"] = {k: _stats(v) for k, v in sorted(out["by_vr"].items())}
    return out


def chase_weak_volume(chg_min: float = 5.5, vr_max: float = 1.2,
                      cfg: Optional[Config] = None) -> dict:
    """「已经接近涨停区（涨幅≥5.5%）但量比还不到 1.2」的交集样本。

    单个条件都不是坑：涨幅 5.5~7.5% 的 19 条挂单口径 17 条赚、量比<1.2 的 5 条 4 条赚；
    交集目前 2 条 1 胜、均值 -2.0%（骏亚科技 -6% 止损、贤丰控股 +2.1%）。n=2 说明不了
    什么，所以只在 explain 里提示、不参与评分，等样本够了再决定要不要变成闸。
    """
    cfg = cfg or load_config()
    rows = [r for r in load_valid_records(cfg) if r.get("exit_t3") is not None
            and r.get("chg_pct") is not None and r.get("vol_ratio") is not None
            and float(r["chg_pct"]) >= chg_min and float(r["vol_ratio"]) < vr_max]
    win = sum(1 for r in rows if float(r["exit_t3"]) > 0)
    avg = sum(float(r["exit_t3"]) for r in rows) / len(rows) if rows else 0.0
    return {"n": len(rows), "win": win, "avg": avg, "chg_min": chg_min, "vr_max": vr_max,
            "codes": [r.get("code") for r in rows]}


def format_exit_status(cfg: Optional[Config] = None) -> str:
    """挂单成交口径一行：止损率/止盈率/平均收益，和「收盘涨没涨」并排看。"""
    st = exit_stats(cfg)
    parts = []
    for key in ("exit_t1", "exit_t3", "exit_t5"):
        s = st.get(key) or {}
        if not s.get("n"):
            continue
        parts.append(f"{s['label']} 均 {s['avg']:+.1f}%｜止损 {s['sl_hit']}｜"
                     f"止盈 {s['tp_hit']}｜赚 {s['win']}/{s['n']}")
    if not parts:
        return "挂单成交口径：暂无可模拟样本（缺 sl_pct 或未回填）"
    return "挂单成交口径（止损价优先、同根 K 线按先止损）：" + "｜".join(parts)


def format_pick_verdict(ev: dict, cfg: Optional[Config] = None) -> List[str]:
    """「这只票今天能不能买」的复盘口径：当前判定 + 同赛道同阶段的历史。

    只对照、不改判定：判定仍来自预审，这里补的是「历史上长得像它的票后来怎样」。
    """
    cfg = cfg or load_config()
    lines: List[str] = []
    verdict = ev.get("verdict")
    total, th = ev.get("total_score"), ev.get("pass_threshold")
    core, core_th = ev.get("core_score"), ev.get("core_threshold")
    lines.append(f"判定：{verdict}｜共振 {total}/{th}" + (
        f"（有效分 {core}/{core_th}）" if core is not None else ""))
    for r in ev.get("reasons") or []:
        lines.append(f"  · {r}")
    sev = ev.get("sector") or {}
    stage = (ev.get("stage") or {}).get("stage")
    chg = (ev.get("quote") or {}).get("chg_pct")
    vr = (ev.get("quote") or {}).get("vol_ratio")
    pool = [r for r in load_valid_records(cfg) if r.get("chg_t3") is not None]
    same_sec = [r for r in pool if sev.get("name") and r.get("sector_name") == sev.get("name")]
    same_stage = [r for r in pool if stage and r.get("stage") == stage]
    for rows, label in ((same_sec, f"同赛道「{sev.get('name')}」"),
                        (same_stage, f"同阶段「{stage}」")):
        if not rows:
            lines.append(f"  · {label}：无历史样本")
            continue
        up = sum(1 for r in rows if float(r["chg_t3"]) > 0)
        why = [f"{r['code']}{r.get('name') or ''} {r.get('date')} "
               f"涨幅{(r.get('chg_pct') or 0):+.1f}%/量比{utils.num(r.get('vol_ratio'))}"
               f"→T+3{r['chg_t3']:+.1f}%" for r in rows]
        lines.append(f"  · {label}：{up}/{len(rows)} T+3>0 = {up / len(rows):.0%}"
                     f"（{'；'.join(why[:4])}）")
    cond = horizon_conditional(cfg=cfg)
    lo, hi = cond["range"]
    if chg is not None and lo <= float(chg) < hi and cond["all"]["n"]:
        a = cond["all"]
        lines.append(f"  · 涨幅 {lo:g}~{hi:g}% 档：{a['up']}/{a['n']} T+3>0 = {a['rate']:.0%}")
        for name in ("by_sector", "by_vr"):
            for k, v in cond[name].items():
                if v["n"] and v["rate"] is not None:
                    lines.append(f"      {k}：{v['up']}/{v['n']} = {v['rate']:.0%}"
                                 f"（均 {v['avg']:+.1f}%）")
    if vr is not None:
        weak = cond["by_vr"].get("量比<1.2")
        note = (f"（量比<1.2 档 {weak['up']}/{weak['n']} T+3>0）"
                if weak and weak["n"] else "")
        lines.append(f"  · 量比 {utils.num(vr)}{note}")
        both = chase_weak_volume(cfg=cfg)
        if (chg is not None and float(chg) >= both["chg_min"]
                and float(vr) < both["vr_max"] and both["n"]):
            caveat = "样本太少，只当提示" if both["n"] < 3 else "两条件单看都不差，叠起来是「高开续涨但资金没跟」"
            lines.append(f"  · 高位没量（涨幅≥{both['chg_min']:g}% 且 量比<{both['vr_max']:g}）："
                         f"{both['n']} 条历史样本 {both['win']}/{both['n']}，"
                         f"均 {both['avg']:+.1f}%——{caveat}")
    return lines


def exit_stats(cfg: Optional[Config] = None) -> dict:
    """「持有 3 天、止损止盈挂单」的实际成交口径：止损率、止盈率、平均收益。

    和 ``horizon_winrates`` 的区别：那边只看收盘涨没涨，这边看挂单能不能兑现——
    按收盘算「55% 涨了」并不等于赚钱。
    """
    cfg = cfg or load_config()
    pool = [r for r in load_valid_records(cfg)
            if r.get("exit_t3") is not None and r.get("sl_pct") is not None]
    out: Dict[str, dict] = {}
    for key, label in (("exit_t1", "T+1"), ("exit_t3", "T+3"), ("exit_t5", "T+5")):
        vals = [float(r[key]) for r in pool if r.get(key) is not None]
        if not vals:
            out[key] = {"n": 0, "label": label}
            continue
        sl_hit = sum(1 for r in pool
                     if r.get(key) is not None and r.get("sl_pct") is not None
                     and float(r[key]) <= -float(r["sl_pct"]) + 0.01)
        tp_hit = sum(1 for r in pool
                     if r.get(key) is not None and r.get("tp_pct") is not None
                     and float(r[key]) >= float(r["tp_pct"]) - 0.01)
        out[key] = {"n": len(vals), "label": label,
                    "avg": sum(vals) / len(vals),
                    "win": sum(1 for v in vals if v > 0),
                    "sl_hit": sl_hit, "tp_hit": tp_hit,
                    "rate": sum(1 for v in vals if v > 0) / len(vals)}
    return out


def _resolve_backfill_date(r: dict, kl: List[dict]) -> Tuple[Optional[int], Optional[str], bool]:
    """定位记录日对应的 K 线；记录日若是周末，顺延到其后第一根 K 线。

    2026-08-08/09 是周六/周日，旧逻辑用记录日找 K 线永远找不到，样本会永久卡在
    待回填。这里只对「周末」顺延；交易日缺 K 线（停牌、数据未到）仍按 pending
    处理，避免把停牌日误当成顺延后的新记录日。

    返回 ``(idx, effective_date, rolled)``；找不到返回 ``(None, None, False)``。
    """
    rec_date = r.get("date")
    idx = next((i for i, k in enumerate(kl) if k.get("date") == rec_date), None)
    if idx is not None:
        return idx, rec_date, False
    d = utils.parse_date(rec_date)
    if not d or utils.is_trading_day(d):
        return None, None, False
    for i, k in enumerate(kl):
        kd = utils.parse_date(k.get("date"))
        if kd and kd > d:
            return i, k.get("date"), True
    return None, None, False


def _trading_days_since(start: Optional[str], today: str) -> int:
    """start 之后到 today（含）之间的交易日数；周末顺延自然计入下一交易日。"""
    d = utils.parse_date(start)
    t = utils.parse_date(today)
    if not d or not t or t <= d:
        return 0
    n = 0
    cur = d
    while cur < t:
        cur += _dt.timedelta(days=1)
        if utils.is_trading_day(cur):
            n += 1
    return n


def _needs_data(r: dict, today: str) -> bool:
    """是否缺已到期行情数据；未到 T+n 的字段不算缺口，避免每天白发请求。"""
    if not r.get("code") or r.get("date") == today:
        return False
    days = _trading_days_since(r.get("date"), today)
    if days <= 0:
        return False
    if r.get("pick_close") is None:
        return True
    if r.get("next_chg") is None or r.get("result") is None:
        return True
    if days >= 2 and r.get("chg_t2") is None:
        return True
    if days >= 3 and (r.get("chg_t3") is None or r.get("result_t3") is None
                      or (r.get("sl_pct") is not None and r.get("exit_t3") is None)):
        return True
    if days >= 5 and (r.get("chg_t5") is None or r.get("result_t5") is None):
        return True
    return False


def _needs_backfill(r: dict, today: str) -> bool:
    """是否需要回填/升级：缺数据或回填版本旧。"""
    if not r.get("code") or r.get("date") == today:
        return False
    return _needs_data(r, today) or r.get("backfill_ver") != BACKFILL_VER


def update_results(market: Optional[Market] = None, cfg: Optional[Config] = None,
                   io: Any = None) -> dict:
    """回填 T+1/T+2/T+3/T+5 多周期结果：用日 K 找记录日之后的第 N 个交易日涨幅。

    「选了 3 天全跌」这类复盘只靠 T+1 看不出来，所以一次把 T+1~T+5 都算好落盘。
    result（win/loss）仍按 T+1 ≥ win_chg_pct 判定，保持既有跟涨胜率口径不变；
    另出 result_t3（T+3 收盘涨≥0 记 win），因为 T+1 与 T+3 符号在 119 条样本里
    有 40 条相反——拿 T+1 的 result 去做 T+3 归因等于两套口径互跳，所以 T+3 的
    成败必须单独落一列，而不是原地改 result 的语义。

    io 传入时每约 10% 报一次进度：几十条 K 线逐条抓会沉默很久，屏上像卡死，
    报个数让人知道还在跑。
    """
    cfg = cfg or load_config()
    recs = load_records(cfg)
    if not recs:
        return {"updated": 0, "pending": 0, "total": 0}
    mk = market or Market(cfg)
    win_th = float(cfg.get("followthrough.win_chg_pct", 3.0))
    t3_th = float(cfg.get("followthrough.t3_win_chg_pct", 0.0))
    today = utils.today_str()
    horizons = ((1, "next_chg"), (2, "chg_t2"), (3, "chg_t3"), (5, "chg_t5"))

    # 版本号负责「一次性升级旧口径」；版本一致后只补已到期的缺字段，避免每天把
    # 126 条历史样本的 120 根 K 线全部重抓一遍。
    needs = [r for r in recs if _needs_backfill(r, today)]
    total = len(needs)
    step = max(1, total // 10) if total else 1
    updated = 0
    for done, r in enumerate(needs, 1):
        try:
            # 30 根只够两个月，旧样本会查不到记录日、永远卡在待回填；多要一些让
            # review 能把老样本和止损模拟一次性补齐。
            kl = mk.get_klines(r["code"], limit=120)
        except Exception:
            continue
        idx, eff_date, rolled = _resolve_backfill_date(r, kl)
        if idx is None:
            continue
        # 当日收盘价（K 线）与真实选票价是两个量：老样本只存了扫描价，用它做基准
        # 会把「从选票价算起」的 T+N 偏掉几个点。
        day_close = kl[idx].get("close")
        if day_close is None:
            continue
        r["close"] = day_close
        if rolled:
            # 周末扫描价不是可成交价：以顺延日（周一/节假日后的第一个交易日）
            # 收盘为选票基准，T+N 也从顺延日收盘起算；原始 date/chg_pct 保留作溯源。
            r["backfill_date"] = eff_date
            r["backfill_note"] = f"非交易日顺延至 {eff_date}"
            r["pick_close"] = day_close
            r["pick_price_basis"] = "rolled"
            r["chg_day_date"] = eff_date
        else:
            # 记录日之前的一根：用来判断「现在记的 chg_pct 是不是当天的涨幅」。
            # 不校验就把两天的涨幅拿去回推选票价，pick_close 会离谱到涨 7% 却比收盘低 7%。
            prev_close = kl[idx - 1].get("close") if idx > 0 else None
            prev_date = kl[idx - 1].get("date") if idx > 0 else None
            chg_day = day_close
            if r.get("chg_pct") is not None:
                same_day = (day_close is not None and prev_close is not None
                            and abs((day_close - prev_close) / prev_close * 100
                                    - float(r["chg_pct"])) <= 0.5)
                # 涨幅对不上记录日：只在「记录日」和「前一交易日」两者间取对得上的那个。
                # 早盘扫描的记录日常常在盘前，chg_pct 属于前一交易日；对不上就宁可不推，
                # 因为它可能还混着当日现价，用它回推只会把基准做错。
                if not same_day:
                    ok_prev = (prev_close is not None and prev_date
                               and abs((prev_close - kl[idx - 2].get("close"))
                                       / kl[idx - 2].get("close") * 100
                                       - float(r["chg_pct"])) <= 0.5) if idx > 1 else False
                    chg_day = prev_close if ok_prev else None
            # 盘中记录的现价（basis=recorded）与旧口径遗留（无 basis）都不能当成交基准：
            # 前者离收盘可能差几个点，后者来源不明。按对得上的收盘 /(1+涨幅) 重推，
            # 标 derived 并留下涨幅所属日期，复盘时也分得清哪些是真实成交价。
            if (r.get("pick_close") is None or r.get("pick_price_basis") != "actual"
                    or day_close is None or r.get("chg_day_date") is None):
                pick = _derive_pick_price(chg_day, r.get("chg_pct"))
                if pick:
                    r["pick_close"] = pick
                    r["pick_price_basis"] = "derived"
                    r["chg_day_date"] = (r.get("date") if chg_day is day_close
                                         else prev_date)
        if not r.get("pick_close"):
            continue
        # 基准一律用选票价，但 T+N 仍按「记录日收盘」算：持仓涨跌从次日起计，
        # 盘中到收盘那一段既不是我们能低成本锁定的，也不在「选出后表现」的定义里。
        move_base = day_close or r["pick_close"]
        base = r["pick_close"]
        lows, highs = [], []
        for off, field in horizons:
            if idx + off >= len(kl):
                continue
            k = kl[idx + off]
            # 最低/最高只从次日起算：记录日当天的影线早于我们能按选票价成交，
            # 把它算进止损会把「还没买就止损」的假亏损记在账上。
            if off > 0 and k.get("low") is not None:
                lows.append(float(k["low"]))
            if off > 0 and k.get("high") is not None:
                highs.append(float(k["high"]))
            if not k.get("close"):
                continue
            r[field] = round((k["close"] - move_base) / move_base * 100.0, 2)
            r[f"low_t{off}"] = (round((min(lows) - base) / base * 100.0, 2)
                                if lows else None)
            r[f"high_t{off}"] = (round((max(highs) - base) / base * 100.0, 2)
                                 if highs else None)
            r[f"exit_t{off}"] = _simulate_exit(
                kl[idx + 1:idx + off + 1], base,
                r.get("sl_pct"), r.get("tp_pct"))
        r["backfill_ver"] = BACKFILL_VER
        r.setdefault("data_status", STATUS_VALID)
        if r.get("next_chg") is not None:
            r["result"] = "win" if r["next_chg"] >= win_th else "loss"
        # T+1/T+2/T+3 用同一把尺子（收盘 > 阈值，默认 0），避免「T+1 的 result 按
        # +3%、T+3 按 >0」两套口径互相解释不通。
        for field, flag in _HORIZON_RESULTS:
            val = r.get(field)
            if val is not None:
                r[flag] = "win" if float(val) >= t3_th else "loss"
        updated += 1
        if io is not None and (done % step == 0 or done == total):
            io.say(f"  ⏳ 回填 {done}/{total} 条...")
    if updated:
        save_records(recs, cfg)
    pending = sum(1 for r in recs if _needs_data(r, today))
    return {"updated": updated, "pending": pending, "total": len(recs)}


def pending_backfill(cfg: Optional[Config] = None) -> int:
    """缺行情数据的历史记录数（不含今日），保持「待 T+1 回填」的既有口径。

    回填版本升级由 ``pending_backfill_upgrades`` 单独计数，避免把「版本旧但数据
    已齐」的样本混进待回填提示。
    """
    cfg = cfg or load_config()
    today = utils.today_str()
    return sum(1 for r in load_records(cfg) if _needs_data(r, today))


def pending_backfill_upgrades(cfg: Optional[Config] = None) -> int:
    """数据已齐、只等回填版本升级的历史记录数（不含今日）。"""
    cfg = cfg or load_config()
    today = utils.today_str()
    return sum(1 for r in load_records(cfg)
               if r.get("code") and r.get("date") != today
               and r.get("backfill_ver") != BACKFILL_VER
               and not _needs_data(r, today))


def pending_by_horizon(cfg: Optional[Config] = None) -> Dict[str, int]:
    """按周期统计历史样本缺口（不含今日）：T+1/T+2/T+3/T+5。"""
    cfg = cfg or load_config()
    today = utils.today_str()
    recs = [r for r in load_records(cfg)
            if r.get("code") and r.get("date") != today]
    return {field: sum(1 for r in recs if r.get(field) is None)
            for field, _ in _HORIZON_RESULTS}


# ------------------------------------------------------------------ 聚合

def key_of(stage: Optional[str], tier: Optional[str], track: Optional[str]) -> str:
    return KEY_SEP.join([stage or "-", tier or "-", track or "-"])


def aggregate(cfg: Optional[Config] = None) -> Dict[str, dict]:
    """按 阶段×档位×轨道 聚合 T+1 胜率 + T+1/T+2/T+3 平均涨幅。"""
    cfg = cfg or load_config()
    min_n = int(cfg.get("followthrough.min_samples", 8))
    out: Dict[str, dict] = {}
    for r in load_valid_records(cfg):
        if r.get("result") not in ("win", "loss"):
            continue
        k = key_of(r.get("stage"), r.get("tier"), r.get("track"))
        slot = out.setdefault(k, {"n": 0, "wins": 0, "stage": r.get("stage"),
                                  "tier": r.get("tier"), "track": r.get("track"),
                                  "avg_next_chg": [], "avg_chg_t2": [],
                                  "avg_chg_t3": []})
        slot["n"] += 1
        slot["wins"] += 1 if r["result"] == "win" else 0
        if r.get("next_chg") is not None:
            slot["avg_next_chg"].append(float(r["next_chg"]))
        if r.get("chg_t2") is not None:
            slot["avg_chg_t2"].append(float(r["chg_t2"]))
        if r.get("chg_t3") is not None:
            slot["avg_chg_t3"].append(float(r["chg_t3"]))
    for k, v in out.items():
        v["rate"] = v["wins"] / v["n"] if v["n"] else None
        v["valid"] = v["n"] >= min_n
        v["avg_next_chg"] = utils.mean(v["avg_next_chg"])
        v["avg_chg_t2"] = utils.mean(v["avg_chg_t2"])
        v["avg_chg_t3"] = utils.mean(v["avg_chg_t3"])
    return out


def score_for(stage: Optional[str], tier: Optional[str], track: Optional[str] = None,
              cfg: Optional[Config] = None) -> Tuple[Optional[float], int]:
    """跟涨分（= 经验 T+1 胜率），样本不足返回 (None, n)。"""
    cfg = cfg or load_config()
    agg = aggregate(cfg)
    slot = agg.get(key_of(stage, tier, track))
    if slot and slot.get("valid"):
        return slot["rate"], slot["n"]
    # 退化：忽略轨道再聚合
    tot_n = tot_w = 0
    for k, v in agg.items():
        s, t, _ = k.split(KEY_SEP)
        if s == (stage or "-") and t == (tier or "-"):
            tot_n += v["n"]
            tot_w += v["wins"]
    if tot_n >= int(cfg.get("followthrough.min_samples", 8)):
        return tot_w / tot_n, tot_n
    return None, (slot or {}).get("n", 0)


def mult_for(score: Optional[float], cfg: Optional[Config] = None) -> float:
    """跟涨仓位乘数：≥0.45→1.0；≥0.30→0.90；<0.30→0.75；无数据→默认。"""
    cfg = cfg or load_config()
    c = lambda k, d: float(cfg.get(f"followthrough.{k}", d))
    if score is None:
        return c("default_mult", 1.0)
    if score >= c("score_high", 0.45):
        return c("mult_high", 1.0)
    if score >= c("score_mid", 0.30):
        return c("mult_mid", 0.90)
    return c("mult_low", 0.75)


def evaluate(stage: Optional[str], tier: Optional[str], track: Optional[str] = None,
             cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    score, n = score_for(stage, tier, track, cfg)
    mult = mult_for(score, cfg)
    return {
        "stage": stage, "tier": tier, "track": track,
        "score": round(score, 4) if score is not None else None,
        "samples": n, "mult": mult,
        "note": (f"跟涨分 {score:.0%}（{n} 样本）→ 乘数 {mult}" if score is not None
                 else f"跟涨样本不足（{n}/{cfg.get('followthrough.min_samples', 8)}）→ 乘数 {mult}"),
    }


# ------------------------------------------------------------------ 观察触发

def trigger_conditions(ev: dict, cfg: Optional[Config] = None) -> List[str]:
    """生成观察轨触发条件（回踩就绪判定的人类可读版本）。"""
    cfg = cfg or load_config()
    conds: List[str] = []
    lo = float(cfg.get("seed.strict_min_chg", 3.0))
    hi = float(cfg.get("seed.strict_max_chg", 5.5))
    intr = float(cfg.get("watch.pullback_max_intraday", 0.65))
    drop = float(cfg.get("watch.pullback_min_drop_pct", 3.0))
    chg = (ev.get("quote") or {}).get("chg_pct")
    vt = ev.get("veto") or {}
    soft = [i["name"] for i in vt.get("soft", [])]

    if chg is not None and chg < lo:
        conds.append(f"涨入严格窗 {lo}~{hi}%")
    if "intraday_high" in soft or "chase_high" in soft:
        conds.append(f"分时回落至 ≤{intr:.0%}")
    if "bias_ma20" in soft:
        conds.append(f"乖离收敛（回撤 ≥{drop:.0f}% 或贴近 MA20）")
    if "near_limit_up" in soft:
        conds.append("次日不封板且回踩不破 5 日线")
    if ev.get("gap", 0) and ev.get("gap") > 0:
        conds.append(f"共振分补足 {ev['gap']} 分（门槛 {ev.get('pass_threshold')}）")
    if not conds:
        conds.append(f"回踩 ≥{drop:.0f}% 且分时 ≤{intr:.0%} 后复评")
    return conds


def format_followthrough(cfg: Optional[Config] = None) -> str:
    agg = aggregate(cfg)
    if not agg:
        return "跟涨经验：暂无样本"
    lines = ["===== 跟涨经验胜率（阶段×档位×轨道）====="]
    for k, v in sorted(agg.items(), key=lambda x: -x[1]["n"]):
        rate = f"{v['rate']:.0%}" if v.get("rate") is not None else "—"
        lines.append(f"  {k.replace(KEY_SEP, ' / ')}: {v['wins']}/{v['n']} = {rate}"
                     f"{'（有效）' if v.get('valid') else '（样本不足）'}"
                     f" 次日均涨 {utils.pct(v.get('avg_next_chg'))}"
                     f"　T+2均涨 {utils.pct(v.get('avg_chg_t2'))}"
                     f"　T+3均涨 {utils.pct(v.get('avg_chg_t3'))}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 阶段 B 样本盘点

def stage_b_bucket_stats(cfg: Optional[Config] = None, min_samples: int = 30) -> dict:
    """阶段 B 经验先验的样本盘点。

    按「板块排名桶 × 阶段 × 档位 × 身份分桶」聚合已回填的 T+1 样本，返回最大桶的
    样本数与是否达标——阶段 B（经验胜率先验）要求单个交叉桶 ≥ min_samples 才可信。
    """
    cfg = cfg or load_config()
    buckets: Dict[str, dict] = {}
    for r in load_valid_records(cfg):
        if r.get("result") not in ("win", "loss"):
            continue
        rank = r.get("sector_rank")
        if rank is None:
            continue
        rank_bucket = ("1-3" if rank <= 3 else "4-5" if rank <= 5
                       else "6-8" if rank <= 8 else ">8")
        isc = r.get("identity_score")
        id_bucket = ("<75" if isc is None or isc < 75 else "75-85" if isc < 85
                     else "85-93" if isc < 93 else "93+")
        key = " / ".join([rank_bucket, r.get("stage") or "-", r.get("tier") or "-", id_bucket])
        slot = buckets.setdefault(key, {"n": 0, "wins": 0})
        slot["n"] += 1
        slot["wins"] += 1 if r["result"] == "win" else 0
    if not buckets:
        return {"max_n": 0, "max_bucket": None, "max_rate": None,
                "threshold": min_samples, "ready": False, "bucket_count": 0}
    max_key = max(buckets, key=lambda k: buckets[k]["n"])
    slot = buckets[max_key]
    return {
        "max_n": slot["n"], "max_bucket": max_key,
        "max_rate": slot["wins"] / slot["n"],
        "threshold": min_samples, "ready": slot["n"] >= min_samples,
        "bucket_count": len(buckets),
    }


def format_stage_b_status(cfg: Optional[Config] = None, min_samples: int = 30) -> str:
    """阶段 B 触发条件提示：review 时打印最大桶样本数，判断何时能上线经验先验。"""
    st = stage_b_bucket_stats(cfg, min_samples)
    if st["max_n"] == 0:
        return "阶段 B 经验先验：暂无回填样本（先跑 seed-plan 攒样本，再跑 review 回填）"
    if st["ready"]:
        return (f"阶段 B 经验先验：最大桶样本 {st['max_n']} ≥ {st['threshold']} 已达标，"
                f"可开始实现（{st['max_bucket']}，胜率 {st['max_rate']:.0%}）")
    return (f"阶段 B 经验先验：最大桶样本 {st['max_n']}/{st['threshold']}"
            f"（{st['max_bucket']}，胜率 {st['max_rate']:.0%}），"
            f"还差 {st['threshold'] - st['max_n']} 条达标")


def lowbuy_sample_stats(cfg: Optional[Config] = None) -> dict:
    """低吸（启动前夕）样本计数：seed_records 里 lowbuy=True 的样本，含回填进度。"""
    cfg = cfg or load_config()
    records = load_records(cfg)
    lowbuy = [r for r in records if r.get("lowbuy")]
    backfilled = [r for r in lowbuy if r.get("result") in ("win", "loss")]
    return {
        "total": len(lowbuy),
        "backfilled": len(backfilled),
        "wins": sum(1 for r in backfilled if r["result"] == "win"),
    }


def format_lowbuy_status(cfg: Optional[Config] = None) -> str:
    """低吸样本进度提示：review 时打印，判断何时能进入低吸验证（≥30 条）。"""
    st = lowbuy_sample_stats(cfg)
    if st["total"] == 0:
        return "低吸样本：0 条（跑 seed-plan 积累启动前夕候选，再跑 review 回填）"
    if st["backfilled"] == 0:
        return f"低吸样本：累计 {st['total']} 条，尚无回填（跑 review 回填 T+N）"
    return (f"低吸样本：累计 {st['total']} 条，已回填 {st['backfilled']} 条"
            f"（胜 {st['wins']}/{st['backfilled']}），目标 ≥30 条进入验证")


# ------------------------------------------------------------------ 样本缺口看板 + 影子桶 T+3

def sample_gap_stats(cfg: Optional[Config] = None) -> dict:
    """样本缺口看板：待 T+1 / 待 T+3、低吸、因子覆盖、新闸门后可买。

    只读统计，不改策略。今日未回填不算进「历史待 T+1」。
    """
    cfg = cfg or load_config()
    today = utils.today_str()
    gate = str(cfg.get("followthrough.p0_gate_date", "2026-08-26") or "2026-08-26")
    recs = load_valid_records(cfg)
    pending_t1 = pending_t2 = pending_t3 = today_waiting = 0
    factor_ok = 0
    post_buyable = 0
    post_buyable_t3 = 0
    for r in recs:
        if not r.get("code"):
            continue
        d = r.get("date") or ""
        if d == today:
            today_waiting += 1
        else:
            # 三个周期各自计数：T+1 已回填不等于 T+3 已到期/已回填。
            if r.get("result") is None:
                pending_t1 += 1
            if r.get("chg_t2") is None:
                pending_t2 += 1
            if r.get("chg_t3") is None:
                pending_t3 += 1
        if r.get("bias_ma20") is not None:
            factor_ok += 1
        if r.get("track") == "可买" and d >= gate:
            post_buyable += 1
            if r.get("chg_t3") is not None:
                post_buyable_t3 += 1
    lb = lowbuy_sample_stats(cfg)
    health = data_health(cfg)
    return {
        "total": len(recs),
        "legacy": health["legacy"],
        "degraded": health["degraded"],
        "all_total": health["total"],
        "pending_t1": pending_t1,
        "pending_t2": pending_t2,
        "pending_t3": pending_t3,
        "today_waiting": today_waiting,
        "lowbuy_total": lb["total"],
        "lowbuy_backfilled": lb["backfilled"],
        "factor_ok": factor_ok,
        "factor_pct": (factor_ok / len(recs)) if recs else 0.0,
        "p0_gate_date": gate,
        "post_gate_buyable": post_buyable,
        "post_gate_buyable_t3": post_buyable_t3,
    }


def format_sample_gap(cfg: Optional[Config] = None) -> str:
    """一行可读的样本缺口摘要（review / followthrough 打印）。"""
    st = sample_gap_stats(cfg)
    factor = f"{st['factor_ok']}/{st['total']}"
    if st["total"]:
        factor += f"（{st['factor_pct']:.0%}）"
    lines = [
        "===== 样本缺口看板 =====",
        (f"  累计 {st['total']} 条｜待 T+1 {st['pending_t1']}｜"
         f"待 T+2 {st['pending_t2']}｜待 T+3 {st['pending_t3']}｜"
         f"今日待下一交易日 {st['today_waiting']}"),
        (f"  低吸 {st['lowbuy_total']} 条（已回填 {st['lowbuy_backfilled']}）｜"
         f"因子字段齐全 {factor}"),
        (f"  新闸门后（≥{st['p0_gate_date']}）可买 {st['post_gate_buyable']} 条"
         f"（其中已有 T+3 {st['post_gate_buyable_t3']}）"),
    ]
    if st.get("degraded"):
        lines.append(f"  降级数据 {st['degraded']} 条（已排除胜率统计）")
    return "\n".join(lines)


def shadow_t3_stats(cfg: Optional[Config] = None) -> dict:
    """影子桶「萌芽∪前三非突破」的 T+3>0 对照（不驱动计划）。

    验收门槛默认 t3_up_target=0.60；样本不足时 ready=False。
    """
    cfg = cfg or load_config()
    target = float(cfg.get("followthrough.t3_up_target", 0.60))
    min_n = int(cfg.get("followthrough.shadow_min_samples", 15))
    tagged: List[dict] = []
    for r in load_valid_records(cfg):
        tag = resolve_shadow_tag(r)
        if not tag:
            continue
        tagged.append({**r, "_shadow_tag": tag})
    with_t3 = [r for r in tagged if r.get("chg_t3") is not None]
    t3_up = sum(1 for r in with_t3 if float(r["chg_t3"]) > 0)
    rate = (t3_up / len(with_t3)) if with_t3 else None
    # 分标签粗看（同一条可属多标签，分母按标签各自计）
    by_tag: Dict[str, dict] = {}
    for r in with_t3:
        for part in str(r["_shadow_tag"]).split("|"):
            if not part:
                continue
            slot = by_tag.setdefault(part, {"n": 0, "up": 0})
            slot["n"] += 1
            if float(r["chg_t3"]) > 0:
                slot["up"] += 1
    return {
        "n_tagged": len(tagged),
        "n_t3": len(with_t3),
        "t3_up": t3_up,
        "t3_up_rate": rate,
        "target": target,
        "min_samples": min_n,
        "ready": bool(rate is not None and len(with_t3) >= min_n and rate >= target),
        "by_tag": by_tag,
    }


def _empty_mode_slot() -> dict:
    return {
        "n": 0, "t1_n": 0, "t1_wins": 0,
        "t3_n": 0, "t3_up": 0,
        "buyable_n": 0, "watch_n": 0,
        "score_met_n": 0, "gate_blocked_n": 0,
        "gate_reasons": {},
    }


def mode_channel_stats(cfg: Optional[Config] = None) -> Dict[str, dict]:
    """rule vs winrate 通道对照：T+1 胜率、T+3>0、硬闸挡下高分样本。

    只读统计，不改闸门；供周报与 B-P1-01 影子对照。
    """
    cfg = cfg or load_config()
    out: Dict[str, dict] = {"rule": _empty_mode_slot(), "winrate": _empty_mode_slot()}
    for r in load_valid_records(cfg):
        mode = r.get("mode") or "rule"
        if mode not in out:
            out[mode] = _empty_mode_slot()
        slot = out[mode]
        slot["n"] += 1
        track = r.get("track")
        if track == "可买":
            slot["buyable_n"] += 1
        elif track:
            slot["watch_n"] += 1
        if r.get("winrate_would_buy"):
            slot["score_met_n"] += 1
        gate = r.get("winrate_gate")
        if gate:
            if r.get("winrate_would_buy"):
                slot["gate_blocked_n"] += 1
            reasons = slot["gate_reasons"]
            key = str(gate).split("→")[0].strip() or str(gate)
            reasons[key] = reasons.get(key, 0) + 1
        if r.get("result") in ("win", "loss"):
            slot["t1_n"] += 1
            if r["result"] == "win":
                slot["t1_wins"] += 1
        if r.get("chg_t3") is not None:
            slot["t3_n"] += 1
            if float(r["chg_t3"]) > 0:
                slot["t3_up"] += 1
    for slot in out.values():
        t1_n = slot["t1_n"]
        t3_n = slot["t3_n"]
        slot["t1_rate"] = (slot["t1_wins"] / t1_n) if t1_n else None
        slot["t3_up_rate"] = (slot["t3_up"] / t3_n) if t3_n else None
    return out


def gate_blocked_recent(cfg: Optional[Config] = None, since_ymd: Optional[str] = None,
                        limit: int = 10) -> List[dict]:
    """高分被硬闸挡下样本（`winrate_would_buy` 且 `winrate_gate`），供周报误杀复核。

    优先展示 T+3 已回填且涨幅更高的行，便于对照「挡下后是否仍走强」。
    """
    cfg = cfg or load_config()
    rows: List[dict] = []
    for r in load_valid_records(cfg):
        if not r.get("winrate_would_buy") or not r.get("winrate_gate"):
            continue
        d = r.get("date") or ""
        if since_ymd and d < since_ymd:
            continue
        t3 = r.get("chg_t3")
        rows.append({
            "date": d,
            "code": r.get("code"),
            "name": r.get("name"),
            "mode": r.get("mode") or "rule",
            "winrate_score": r.get("winrate_score"),
            "winrate_gate": r.get("winrate_gate"),
            "sector_rank": r.get("sector_rank") or r.get("pick_sector_rank"),
            "chg_t3": t3,
        })

    def _sort_key(row: dict) -> tuple:
        t3 = row.get("chg_t3")
        return (t3 is not None, float(t3) if t3 is not None else -999.0, row.get("date") or "")

    rows.sort(key=_sort_key, reverse=True)
    return rows[: max(0, int(limit))]


def format_gate_blocked_recent(cfg: Optional[Config] = None, since_ymd: Optional[str] = None,
                               limit: int = 8) -> str:
    """硬闸挡下高分样本的可读列表（控制台/邮件用）。"""
    rows = gate_blocked_recent(cfg, since_ymd=since_ymd, limit=limit)
    if not rows:
        return ""
    lines = ["硬闸挡下高分样本（只记录，不据此放宽）"]
    for r in rows:
        t3 = r.get("chg_t3")
        t3_txt = f"T+3 {float(t3):+.1f}%" if t3 is not None else "T+3 —"
        gate = str(r.get("winrate_gate") or "")[:28]
        lines.append(
            f"  {r.get('date')} {r.get('code')} {r.get('name')}  "
            f"分{r.get('winrate_score')}  rank{r.get('sector_rank')}  {t3_txt}  {gate}")
    return "\n".join(lines)


def format_mode_channel_stats(cfg: Optional[Config] = None, compact: bool = False) -> str:
    """rule vs winrate 对照可读文案。"""
    st = mode_channel_stats(cfg)
    lines = ["===== rule vs winrate 通道对照 ====="]
    for mode, label in (("rule", "规则(9分共振)"), ("winrate", "胜率影子")):
        slot = st.get(mode) or _empty_mode_slot()
        if not slot.get("n"):
            lines.append(f"  {label}：暂无样本")
            continue
        t1 = slot.get("t1_rate")
        t3 = slot.get("t3_up_rate")
        t1_txt = f"T+1 {t1:.0%}（{slot['t1_wins']}/{slot['t1_n']}）" if t1 is not None else "T+1 —"
        t3_txt = (f"T+3>0 {t3:.0%}（{slot['t3_up']}/{slot['t3_n']}）"
                  if t3 is not None else "T+3 —")
        lines.append(
            f"  {label}：{slot['n']} 条｜可买 {slot['buyable_n']}｜观察 {slot['watch_n']}"
            f"｜{t1_txt}｜{t3_txt}")
        if slot.get("gate_blocked_n"):
            lines.append(f"    高分被硬闸挡 {slot['gate_blocked_n']} 条")
        if not compact and slot.get("gate_reasons"):
            parts = [f"{k} {v}" for k, v in sorted(slot["gate_reasons"].items(),
                                                   key=lambda x: -x[1])[:5]]
            lines.append(f"    硬闸 TOP：{'；'.join(parts)}")
    return "\n".join(lines)


def _t3_bucket_rank(rank: Optional[int]) -> str:
    if rank is None:
        return "rank未知"
    if rank <= 3:
        return "rank≤3"
    if rank <= 5:
        return "rank4-5"
    return "rank>5"


def _t3_bucket_chg(chg: Optional[float]) -> str:
    if chg is None:
        return "涨幅未知"
    if chg < 5.5:
        return "3~5.5%"
    if chg < 7.5:
        return "5.5~7.5%"
    return ">7.5%"


#: 共振分里「真正会变」的维度。消息面在自动扫描下恒为 0/0（max=0 已不占满分）；
#: 止损结构在止损规则内恒为 +1/+1——两维对排序零贡献，只把满分垫高，让「6/9」
#: 看起来比实际宽松。有效分只数这两类之外的分，便于看出门槛到底意味着什么。
_VOLATILE_DIMS = ("板块强度", "大盘趋势", "市值区间", "量价结构")


def resonance_core_score(dims: Optional[List[dict]]) -> Tuple[Optional[int], Optional[int]]:
    """从共振六维里取「有效分/有效满分」：只数会变化的维度。"""
    if not dims:
        return None, None
    core = [d for d in dims if d.get("name") in _VOLATILE_DIMS]
    if not core:
        return None, None
    return (int(sum(int(d.get("score") or 0) for d in core)),
            int(sum(int(d.get("max") or 0) for d in core)))


def _t3_bucket_res(dims: Optional[List[dict]]) -> Optional[str]:
    """有效分桶：把 0~6 的有效分压成三档，避免桶太碎每条只有 1~2 样本。"""
    core, _ = resonance_core_score(dims)
    if core is None:
        return None
    if core >= 6:
        return "有效6"
    if core >= 4:
        return "有效4-5"
    return "有效≤3"


def t3_attribution(cfg: Optional[Config] = None) -> dict:
    """多维 T+3>0 归因（方案 E：只读报告，不改闸门）。

    维度：板块排名 / 阶段 / 涨幅桶 / 轨道 / 共振有效分 + 影子桶摘要。
    """
    cfg = cfg or load_config()
    dims: Dict[str, Dict[str, dict]] = {
        "rank": {}, "stage": {}, "chg": {}, "track": {}, "res": {},
    }
    diff_n = diff_mismatch = 0
    total_n = total_up = 0
    mengya_n = mengya_up = 0
    for r in load_valid_records(cfg):
        if r.get("chg_t3") is None:
            continue
        up = float(r["chg_t3"]) > 0
        total_n += 1
        total_up += 1 if up else 0

        def _slot(bucket_map: Dict[str, dict], key: str) -> dict:
            return bucket_map.setdefault(key, {"n": 0, "up": 0})

        rk = _t3_bucket_rank(r.get("sector_rank"))
        _slot(dims["rank"], rk)["n"] += 1
        _slot(dims["rank"], rk)["up"] += 1 if up else 0

        res_b = _t3_bucket_res(r.get("scoring_dims"))
        if res_b is not None:
            _slot(dims["res"], res_b)["n"] += 1
            _slot(dims["res"], res_b)["up"] += 1 if up else 0

        # T+1 的 result 与 T+3 方向不同的比例：这个数越大，「按 T+1 胜率选票、
        # 按 T+3 复盘」就越像两套系统各说各话。
        if r.get("result") in ("win", "loss"):
            diff_n += 1
            if (r["result"] == "win") != up:
                diff_mismatch += 1

        stage = str(r.get("stage") or "-")
        _slot(dims["stage"], stage)["n"] += 1
        _slot(dims["stage"], stage)["up"] += 1 if up else 0
        if stage == "萌芽":
            mengya_n += 1
            mengya_up += 1 if up else 0

        chg_b = _t3_bucket_chg(r.get("chg_pct"))
        _slot(dims["chg"], chg_b)["n"] += 1
        _slot(dims["chg"], chg_b)["up"] += 1 if up else 0

        track = str(r.get("track") or "-")
        _slot(dims["track"], track)["n"] += 1
        _slot(dims["track"], track)["up"] += 1 if up else 0

    def _finalize(bucket_map: Dict[str, dict]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for k, v in bucket_map.items():
            n, up = v["n"], v["up"]
            out[k] = {"n": n, "up": up, "rate": (up / n) if n else None}
        return out

    shadow = shadow_t3_stats(cfg)
    return {
        "total_n": total_n,
        "total_up": total_up,
        "total_rate": (total_up / total_n) if total_n else None,
        "mengya_n": mengya_n,
        "mengya_up": mengya_up,
        "mengya_rate": (mengya_up / mengya_n) if mengya_n else None,
        "rank": _finalize(dims["rank"]),
        "stage": _finalize(dims["stage"]),
        "chg": _finalize(dims["chg"]),
        "track": _finalize(dims["track"]),
        "res": _finalize(dims["res"]),
        "t1_t3_disagree_n": diff_mismatch,
        "t1_t3_compare_n": diff_n,
        "t1_t3_disagree_rate": (diff_mismatch / diff_n) if diff_n else None,
        "shadow": shadow,
        "gap": sample_gap_stats(cfg),
    }


def format_t3_attribution(cfg: Optional[Config] = None, compact: bool = False) -> str:
    """T+3 归因可读文案（review / 周报摘要用）。"""
    st = t3_attribution(cfg)
    if st["total_n"] == 0:
        return "T+3 归因：尚无回填样本（跑 review 补 T+3）"
    rate = st["total_rate"] or 0.0
    lines = [
        f"T+3>0 全样本：{st['total_up']}/{st['total_n']} = {rate:.0%}",
    ]
    if st["mengya_n"]:
        mr = st["mengya_rate"] or 0.0
        lines.append(f"  萌芽专看 T+3>0：{st['mengya_up']}/{st['mengya_n']} = {mr:.0%}")
    if st.get("t1_t3_compare_n"):
        lines.append(f"  T+1 与 T+3 方向不一致：{st['t1_t3_disagree_n']}/{st['t1_t3_compare_n']}"
                     f" = {st['t1_t3_disagree_rate']:.0%}（说明 result 只看 T+1 不够用）")
    gap = st.get("gap") or {}
    lines.append(f"  待回填：T+1 {gap.get('pending_t1', 0)}｜T+3 {gap.get('pending_t3', 0)}")
    if not compact:
        for dim, title in (("rank", "板块排名"), ("chg", "涨幅桶"), ("stage", "阶段"),
                           ("res", "共振有效分")):
            parts = []
            for k, v in sorted((st.get(dim) or {}).items(), key=lambda x: -x[1]["n"]):
                rr = v["rate"]
                parts.append(f"{k} {v['up']}/{v['n']}={rr:.0%}" if rr is not None else f"{k} n={v['n']}")
            if parts:
                lines.append(f"  {title}：" + "；".join(parts))
        lines.append("  " + format_shadow_status(cfg).replace("\n", " "))
    return "\n".join(lines)


def format_shadow_status(cfg: Optional[Config] = None) -> str:
    """影子桶 T+3 对照文案：对照验收门槛，不暗示可买。"""
    st = shadow_t3_stats(cfg)
    if st["n_tagged"] == 0:
        return ("影子桶（萌芽∪前三非突破）：0 条（跑 seed-plan 落盘 shadow_tag；"
                "只对照不驱动计划）")
    if st["n_t3"] == 0:
        return (f"影子桶：已标 {st['n_tagged']} 条，尚无 T+3 回填"
                f"（目标 T+3>0 ≥{st['target']:.0%}，至少 {st['min_samples']} 条）")
    rate = st["t3_up_rate"] or 0.0
    flag = "达标" if st["ready"] else (
        "样本不足" if st["n_t3"] < st["min_samples"] else "未达门槛")
    parts = []
    for name, slot in sorted(st["by_tag"].items(), key=lambda x: -x[1]["n"]):
        rr = slot["up"] / slot["n"] if slot["n"] else 0.0
        parts.append(f"{name} {slot['up']}/{slot['n']}={rr:.0%}")
    detail = "；".join(parts) if parts else "—"
    return (f"影子桶 T+3>0：{st['t3_up']}/{st['n_t3']} = {rate:.0%}"
            f"（目标 ≥{st['target']:.0%}，{flag}；已标 {st['n_tagged']}）"
            f"　{detail}")


# ------------------------------------------------------------------ T+3 获利概率（胜率通道辅助）

def _t3_rate_lookup(dim: str, bucket: str, attr: dict,
                    cfg: Optional[Config] = None) -> Tuple[Optional[float], int, str]:
    """查单维 T+3>0 比例：优先 live 回填（n≥min_samples），否则 config 默认。"""
    cfg = cfg or load_config()
    min_n = int(cfg.get("winrate.t3_prob.min_samples", 8))
    live = (attr.get(dim) or {}).get(bucket) or {}
    n = int(live.get("n") or 0)
    rate = live.get("rate")
    if n >= min_n and rate is not None:
        return float(rate), n, "回填"
    fb = (cfg.get("winrate.t3_prob.fallback") or {}).get(dim) or {}
    fb_rate = fb.get(bucket)
    if fb_rate is not None:
        return float(fb_rate), 0, "默认"
    total = attr.get("total_rate")
    if total is not None:
        return float(total), int(attr.get("total_n") or 0), "全样本"
    return None, 0, "—"


def _combine_t3_rates(rates: List[float], mode: str) -> Optional[float]:
    if not rates:
        return None
    if mode == "min":
        return min(rates)
    prod = 1.0
    for r in rates:
        prod *= r
    return prod ** (1.0 / len(rates))


def estimate_t3_up_prob(ev: dict, cfg: Optional[Config] = None) -> dict:
    """估算 T+3>0 获利概率（板块/阶段/涨幅桶几何均值，实证对照，非收益承诺）。

    供胜率选股展示与可选硬闸；live 样本够时用 ``t3_attribution``，否则用
    ``winrate.t3_prob.fallback``（来源 docs/archive/FACTOR_ATTRIB_2026-09-04）。
    """
    cfg = cfg or load_config()
    attr = t3_attribution(cfg)
    rank = (ev.get("sector") or {}).get("rank")
    stage = (ev.get("stage") or {}).get("stage") or "-"
    chg = (ev.get("quote") or {}).get("chg_pct")

    factors: List[Tuple[str, str, str]] = [
        ("rank", _t3_bucket_rank(rank), "板块"),
        ("stage", str(stage), "阶段"),
        ("chg", _t3_bucket_chg(chg), "涨幅"),
    ]
    rates: List[float] = []
    parts: List[str] = []
    for dim, bucket, label in factors:
        rate, n, src = _t3_rate_lookup(dim, bucket, attr, cfg)
        if rate is None:
            continue
        rates.append(rate)
        src_note = f"{n}样本" if src == "回填" else src
        parts.append(f"{label}{bucket} {rate:.0%}({src_note})")

    combine = str(cfg.get("winrate.t3_prob.combine") or "geomean")
    prob = _combine_t3_rates(rates, combine)
    min_prob = float(cfg.get("winrate.t3_prob.min_prob", 0.35))
    detail = "；".join(parts)
    if prob is not None:
        detail += f" → 综合{prob:.0%}（{'几何均值' if combine != 'min' else '保守取低'}，非收益承诺）"
    return {
        "prob": round(prob, 4) if prob is not None else None,
        "min_prob": min_prob,
        "parts": parts,
        "detail": detail or "T+3 因子不足",
        "combine": combine,
    }
