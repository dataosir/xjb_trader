"""配置加载/持久化/策略参数。

- 全量默认值集中在 DEFAULTS（分段：paths/market/timing/sentiment/scoring/identity/
  veto/strategy/position/expectancy/followthrough/watch/permissions/report）。
- 用户配置 tea_config.json 与默认值深合并，缺项自动回落默认，新增参数自动生效。
- 原子写入，支持点号路径读写（cfg.get("strategy.pass_threshold")）。
"""
from __future__ import annotations

import copy
import os
from typing import Any, Dict, Optional

from . import utils

CONFIG_ENV = "TEA_CONFIG"
HOME_ENV = "TEA_HOME"
CONFIG_NAME = "tea_config.json"

DEFAULTS: Dict[str, Any] = {
    "version": 1,
    # ---------------------------------------------------------- 路径
    "paths": {
        "data_dir": "data",
        "reports_dir": "reports",
        "plan_file": "trade_plan.json",
        "daily_state_file": "daily_state.json",
        "capital_state_file": "capital_state.json",
        "watch_pool_file": "watch_pool.json",
        "trades_file": "trades.json",
        "seed_records_file": "seed_records.jsonl",
        "seed_trace_jsonl": "seed_trace.jsonl",
        "seed_trace_md": "SEED_TRACE.md",
        "accumulator_file": "accumulator.jsonl",
        "sector_cache_file": ".tea_sector_cache.json",
        "shadow_pool_file": "shadow_pool.json",
    },
    # ---------------------------------------------------------- 行情/防封
    "market": {
        "quote_url": "https://push2.eastmoney.com/api/qt/stock/get",
        "kline_url": "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        "clist_url": "https://push2.eastmoney.com/api/qt/clist/get",
        "ztpool_url": "https://push2ex.eastmoney.com/getTopicZTPool",
        # 涨停池的 ut 令牌与行情接口不同一套。填错不会报 HTTP 错，接口回 rc=205
        # 且 data=null，看上去就像「今天没有涨停」。
        "ztpool_ut": "7eea3edcaed734bea9cbfc24409ed989",
        "quote_fields": "f43,f44,f45,f46,f47,f48,f50,f57,f58,f100,f116,f117,f127,f168,f169,f170",
        "kline_fields1": "f1,f2,f3,f4,f5,f6",
        "kline_fields2": "f51,f52,f53,f54,f55,f56,f57",
        "kline_limit": 30,
        "kline_klt": 101,
        "kline_fqt": 1,
        "index_secid": "1.000001",
        "index_kline_limit": 25,
        "sector_fs": "m:90+t:2",
        "sector_fields": "f3,f12,f14,f104,f105",
        # clist 硬限 100 行/页（pz 填再大也不多给），下面的上限是「最多翻几页」。
        # 实际翻几页看接口自报的 data.total，抽到够数就停。
        "sector_max_pages": 12,
        "member_max_pages": 10,
        "member_fields": "f3,f8,f12,f14,f20",
        "breadth_fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "breadth_flat_eps": 0.05,
        "breadth_max_probes": 24,
        "ztpool_page_size": 300,
        "ztpool_fallback_days": 3,
        # 防封
        "timeout": 8.0,
        "retries": 3,
        "retry_backoff": 1.7,
        "delay_base": 0.35,
        "delay_spread": 0.25,
        "delay_after_error": 1.2,
        "rotate_referer": True,
        "rotate_ua": True,
        "rotate_cdn": True,
        "cdn_hosts_quote": ["push2.eastmoney.com", "82.push2.eastmoney.com", "push2delay.eastmoney.com"],
        "cdn_hosts_kline": ["push2his.eastmoney.com", "1.push2his.eastmoney.com"],
        "referers": [
            "https://quote.eastmoney.com/",
            "https://quote.eastmoney.com/center/gridlist.html",
            "https://data.eastmoney.com/",
            "https://www.eastmoney.com/",
        ],
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        ],
        "proxy_pool": [],
        "proxy_rotate": True,
        # 缓存
        "quote_cache_sec": 20,
        "kline_cache_sec": 300,
        "sector_cache_sec": 600,
        "sector_disk_cache_hours": 24,
        "member_cache_sec": 600,
        "breadth_cache_sec": 120,
        "ztpool_cache_sec": 120,
        "index_cache_sec": 120,
        "offline": False,
    },
    # ---------------------------------------------------------- 交易时间轴
    "timing": {
        "observe_scan": "13:30",
        "seed_scan": "14:30",
        "plan_recheck": "14:35",
        "overnight_review": "10:00",
        "buy_window_start": "14:00",
        "buy_window_end": "14:45",
        "session_am_start": "09:30",
        "session_am_end": "11:30",
        "session_pm_start": "13:00",
        "session_pm_end": "15:00",
        "window_tolerance_min": 0,
        "allow_weekend_ops": False,
    },
    # ---------------------------------------------------------- 道：情绪
    "sentiment": {
        "base_score": 50.0,
        "max_score": 100.0,
        "hot_sector_chg": 3.0,
        "hot_sector_topn": 20,
        "advance_low": 0.35,
        "advance_low_delta": -12.0,
        "advance_high": 0.55,
        "advance_high_delta": 8.0,
        "boards_low": 2,
        "boards_low_delta": -10.0,
        "boards_high": 5,
        "boards_high_delta": 6.0,
        "ma20_above_delta": 12.0,
        "ma20_below_delta": -12.0,
        "index_up_pct": 0.5,
        "index_up_delta": 8.0,
        "index_down_pct": -0.8,
        "index_down_delta": -10.0,
        "hot_n_strong": 8,
        "hot_n_strong_delta": 15.0,
        "hot_n_mid": 4,
        "hot_n_mid_delta": 8.0,
        "hot_n_weak": 1,
        "hot_n_weak_delta": -10.0,
        "avg5_overheat": 6.0,
        "avg5_overheat_delta": -8.0,
        "avg5_healthy_low": 2.0,
        "avg5_healthy_high": 5.0,
        "avg5_healthy_delta": 5.0,
        "cycle_ice_below": 35.0,
        "cycle_repair_below": 50.0,
        "cycle_ferment_below": 70.0,
        "climax_avg5": 6.0,
        "climax_hot_n": 10,
        "ebb_hot_n": 6,
        "ebb_extra_delta": -5.0,
        "stance_empty_below": 40.0,
        "stance_defend_below": 55.0,
        "climax_block_avg5": 7.0,
        "ice_cut_boards": 3,
        "ice_cut_advance": 0.35,
        "ice_cut_mult": 0.25,
        "cache_sec": 120,
    },
    # ---------------------------------------------------------- 术：9 分共振
    "scoring": {
        "max_total": 9,
        # ① 板块强度 2
        "sector_rank_full": 8,
        "sector_limit_up_full": 2,
        "sector_rank_half": 15,
        "sector_limit_up_half": 1,
        "sector_inner_top_pct": 0.10,
        "sector_inner_bonus": 1,
        "sector_inner_tail_pct": 0.50,
        "sector_inner_penalty": 1,
        "sector_dim_cap": 2,
        # ② 大盘趋势 1
        "index_dim_max": 1,
        # ③ 消息面 1
        "news_dim_max": 1,
        # ④ 市值 2
        "cap_ideal_low": 50.0,
        "cap_ideal_high": 300.0,
        "cap_mid_low": 30.0,
        "cap_mid_high": 500.0,
        "cap_small_zero_below": 30.0,
        "cap_huge_score": 1,
        # ⑤ 量价结构 2
        "vp_dim_max": 2,
        "vp_vol_ratio_strong": 1.2,
        "vp_vol_ratio_shrink": 0.8,
        "amount_min_yi": 2.0,
        "amount_max_yi": 80.0,
        "turnover_min_pct": 2.0,
        "turnover_max_pct": 20.0,
        "bias_penalty_pct": 8.0,
        "vp_penalty_each": 1,
        # ⑥ 止损结构 1
        "sl_struct_max_pct": 8.0,
        "sl_struct_atr_mult": 2.5,
        "sl_struct_min_odds": 3.0,
        # 阶段/分时
        "sprout_max_chg": 5.5,
        "intraday_chase_pct": 0.75,
        "intraday_hard_pct": 0.95,
        "intraday_leader_pct": 0.85,
        "intraday_overheat_pct": 0.85,
        "sprout_intraday_tol": 0.92,
        "overheat_bias_normal": 8.0,
        "overheat_bias_sprout_leader": 12.0,
        "overheat_bias_break_leader": 15.0,
        "leader_intraday_relax_score": 90,
        "leader_intraday_relax_rank": 3,
    },
    # ---------------------------------------------------------- 身份判定
    "identity": {
        "base_score": 50.0,
        "inner_top_pct": 0.20,
        "inner_top_delta": 20.0,
        "inner_mid_pct": 0.35,
        "inner_mid_delta": 8.0,
        "inner_tail_pct": 0.50,
        "inner_tail_delta": -25.0,
        "sector_rank_strong": 10,
        "sector_rank_strong_delta": 15.0,
        "sector_rank_mid": 20,
        "sector_rank_mid_delta": 8.0,
        "sector_rank_weak": 30,
        "sector_rank_weak_delta": -15.0,
        "vs_sector_strong_pct": 1.0,
        "vs_sector_strong_delta": 10.0,
        "vs_sector_weak_pct": 1.5,
        "vs_sector_weak_delta": -20.0,
        "limit_up_follow_chg": 7.0,
        "limit_up_follow_delta": 10.0,
        "limit_up_lag_chg": 5.0,
        "limit_up_lag_delta": -15.0,
        "cap_good_low": 50.0,
        "cap_good_high": 200.0,
        "cap_good_delta": 5.0,
        "cap_bad_delta": -10.0,
        "ma_bull_delta": 5.0,
        "ma_weak_delta": -10.0,
        "ma_weak_chg": 3.0,
        "hot_rel_low": 0.42,
        "hot_rel_high": 0.90,
        "hot_rel_ok_delta": 18.0,
        "hot_rel_lag_delta": -15.0,
        "hot_rel_over_delta": -10.0,
        "hot_front_sector_rank": 8,
        "hot_front_inner_pct": 0.40,
        "hot_front_delta": 12.0,
        "flags_force_zamao": 2,
    },
    # ---------------------------------------------------------- VETO
    "veto": {
        "check_st": True,
        "check_board_permission": True,
        "check_limit_up": True,
        "check_turnover": True,
        "check_bias": True,
        "check_intraday": True,
        "soft_items": ["intraday_high", "bias_ma20", "near_limit_up", "chase_high"],
    },
    # ---------------------------------------------------------- 权限
    "permissions": {
        "main": True,
        "gem": True,
        "star": False,
        "bse": False,
    },
    # ---------------------------------------------------------- 法/术：策略主段
    "strategy": {
        "max_position_pct": 0.50,
        "gray_ratio": 0.30,
        "confirm_ratio": 0.70,
        "pass_threshold": 6,
        "min_odds": 3,
        "consec_loss_limit": 2,
        "daily_max_new_trades": 1,
        "daily_max_evaluations": 5,
        "daily_max_symbol_evaluations": 2,

        "seed_min_chg": 3.0,
        "seed_max_chg": 5.5,
        "seed_top_sectors": 3,
        "seed_max_output": 2,
        "seed_min_identity": 70,
        "seed_min_pick_score": 60,
        "seed_min_sector_rank": 8,
        "seed_min_sector_limit_up": 2,
        "seed_cap_max": 300,
        "seed_rank_pct": 0.35,
        "seed_relaxed_max_chg": 7.5,
        "seed_momentum_max_chg": 8.8,
        "seed_hot_sector_chg": 6.0,
        "seed_relative_chg_ratio": 0.42,

        "veto_near_limit_pct": 9.0,
        "veto_limit_zone_pct": 9.5,
        "veto_max_turnover": 25.0,
        "veto_bias_ma20_pct": 8.0,
        "veto_bias_leader_pct": 20.0,
        "veto_bias_normal_pct": 15.0,
        "veto_bias_hard_max_pct": 30.0,
        "veto_intraday_high_pct": 0.75,
        "veto_intraday_hard_pct": 0.95,

        "identity_leader_threshold": 70,
        "identity_follow_threshold": 45,
        "identity_zamao_threshold": 45,
        "identity_zamao_mode": "warn",
        "identity_zamao_pass_bump": 1,

        "atr_sl_mult": 1.5,
        "atr_tp_mult": 3.0,
        "atr_sl_min_pct": 2.0,
        "atr_sl_max_pct": 8.0,
        "atr_sl_hard_max_pct": 6.0,
        "atr_tp_cap_pct": 15.0,

        "plan_max_price_dev_pct": 1.5,
        "plan_max_sector_rank_slip": 8,

        "block_off_window_eval": True,
        "require_plan_for_new_open": True,
        "require_standard_window_for_buy": True,
        "block_new_eval_when_index_below_ma20": True,
        "cancel_high_score_min": 7,
    },
    # ---------------------------------------------------------- 种子扫描细则
    "seed": {
        "sector_scan_topn": 30,
        "rank_score_base": 40.0,
        "rank_score_step": 3.0,
        "limit_up_score_div": 5.0,
        "limit_up_score_cap": 35.0,
        "chg_score_low": 2.0,
        "chg_score_high": 8.0,
        "chg_score_value": 25.0,
        "heat_weight": 0.65,
        "mild_weight": 0.35,
        "mild_chg_low": 3.0,
        "mild_chg_high": 5.5,
        "mild_cap_low": 50.0,
        "mild_cap_high": 300.0,
        "mild_target_ratio": 0.20,
        "sector_relax_score": 60.0,
        "diversify_replace_last": True,
        "shadow_bonus": 18.0,
        "shadow_near_rank": 6,
        "shadow_keep_days": 2,
        # 三档门槛
        "strict_min_chg": 3.0,
        "strict_max_chg": 5.5,
        "strict_min_identity": 70,
        "strict_min_pick": 60,
        "strict_rank_pct": 0.35,
        "strict_min_turnover": 2.0,
        "relaxed_min_chg": 3.0,
        "relaxed_max_chg": 7.5,
        "relaxed_min_identity": 65,
        "relaxed_min_pick": 62,
        "relaxed_rank_pct": 0.40,
        "relaxed_min_turnover": 3.0,
        "relaxed_cap_max": 300.0,
        "momentum_min_chg": 5.0,
        "momentum_max_chg": 8.8,
        "momentum_min_identity": 68,
        "momentum_min_identity_hot": 62,
        "momentum_min_pick": 60,
        "momentum_rank_pct": 0.48,
        "hot_identity_relax": 62,
        "front_row_topk": 3,
        "front_row_min_turnover": 1.5,
        "cap_min": 50.0,
        "cap_max": 300.0,
        "turnover_max": 20.0,
        "leader_pass_bonus": 1,
        "leader_pass_floor": 6,
        "near_miss_gap": 1,
        "max_watch_output": 3,
        "max_near_miss_output": 6,
        "eve_min_chg": 1.0,
        "eve_max_chg": 3.0,
        "eve_trigger_intraday": 0.75,
        "sprout_scan_enabled": True,
        "member_fetch_cap": 60,
        "candidate_fetch_cap": 30,
    },
    # ---------------------------------------------------------- 仓位
    "position": {
        "lot_size": 100,
        "half_pos_mult_min": 0.25,
        "half_pos_mult_max": 1.0,
        "slippage_buy_pct": 0.5,
        "slippage_stop_pct": 0.5,
        "fee_rate": 0.00025,
        "min_fee": 5.0,
        "stamp_tax": 0.0005,
        "min_shares": 100,
        "fallback_sl_pct": 3.0,
    },
    # ---------------------------------------------------------- 期望值
    "expectancy": {
        "min_samples": 5,
        "bucket_size": 1,
        "win_chg_pct": 3.0,
        "mult_high_er": 0.5,
        "mult_high": 1.0,
        "mult_mid_er": 0.2,
        "mult_mid": 0.85,
        "mult_low_er": 0.0,
        "mult_low": 0.70,
        "mult_neg": 0.50,
        "mult_insufficient": 0.85,
        "default_win_rate": 0.40,
        "insufficient_pass_bump": 1,
    },
    # ---------------------------------------------------------- 跟涨经验
    "followthrough": {
        "min_samples": 8,
        "win_chg_pct": 3.0,
        "score_high": 0.45,
        "mult_high": 1.0,
        "score_mid": 0.30,
        "mult_mid": 0.90,
        "mult_low": 0.75,
        "default_score": None,
        "default_mult": 1.0,
    },
    # ---------------------------------------------------------- 观察池
    "watch": {
        "max_size": 12,
        "trend_keep_days": 2,
        "confirm_keep_days": 3,
        "pullback_min_drop_pct": 3.0,
        "pullback_max_intraday": 0.65,
        "pullback_keep_days": 3,
        "auto_prune_on_review": True,
        "tracks": ["趋势轨", "观察轨", "启动待定轨", "萌芽观察轨", "前夕观察轨"],
    },
    # ---------------------------------------------------------- 报告
    "report": {
        "write_trade_check": True,
        "write_seed_report": True,
        "write_seed_trace": True,
        "trade_check_prefix": "TRADE_CHECK",
        "seed_prefix": "SEED",
        "weekly_prefix": "WEEKLY",
        "keep_reports": 200,
    },
    # ---------------------------------------------------------- 交互
    "ui": {
        "lang": "zh",
        "color": True,
        "confirm_buy": True,
        "show_debug": False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


_MISSING = object()


def count_params(node: Any = _MISSING) -> int:
    """统计可调参数个数（叶子节点计数）。"""
    if node is _MISSING:
        node = DEFAULTS
    if isinstance(node, dict):
        return sum(count_params(v) for v in node.values())
    if isinstance(node, list):
        return 1
    return 1


def home_dir() -> str:
    return os.environ.get(HOME_ENV) or os.getcwd()


def config_path() -> str:
    return os.environ.get(CONFIG_ENV) or os.path.join(home_dir(), CONFIG_NAME)


class Config:
    """配置容器：点号路径读写 + 原子持久化。"""

    def __init__(self, data: Optional[dict] = None, path: Optional[str] = None):
        self.path = path or config_path()
        self.data = _deep_merge(DEFAULTS, data or {})

    # -------------------------------------------------- 读写
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def s(self, key: str, default: Any = None) -> Any:
        """strategy 段快捷读取。"""
        return self.get(f"strategy.{key}", default)

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self.data
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value

    # -------------------------------------------------- 路径
    def data_dir(self) -> str:
        d = self.get("paths.data_dir", "data")
        if not os.path.isabs(d):
            d = os.path.join(home_dir(), d)
        return utils.ensure_dir(d)

    def reports_dir(self) -> str:
        d = self.get("paths.reports_dir", "reports")
        if not os.path.isabs(d):
            d = os.path.join(home_dir(), d)
        return utils.ensure_dir(d)

    def data_file(self, key: str) -> str:
        name = self.get(f"paths.{key}") or key
        return os.path.join(self.data_dir(), name)

    def report_file(self, name: str) -> str:
        return os.path.join(self.reports_dir(), name)

    # -------------------------------------------------- 持久化
    def save(self) -> str:
        return utils.write_json(self.path, self.data)

    def as_dict(self) -> dict:
        return copy.deepcopy(self.data)


_CACHED: Optional[Config] = None


def load_config(path: Optional[str] = None, reload: bool = False) -> Config:
    """加载配置（进程内缓存）。"""
    global _CACHED
    if _CACHED is not None and not reload and path is None:
        return _CACHED
    p = path or config_path()
    raw = utils.read_json(p, default={}) or {}
    cfg = Config(raw, path=p)
    if path is None:
        _CACHED = cfg
    return cfg


def save_config(cfg: Config) -> str:
    return cfg.save()


def reset_config(path: Optional[str] = None) -> Config:
    cfg = Config({}, path=path)
    cfg.save()
    global _CACHED
    _CACHED = cfg
    return cfg
