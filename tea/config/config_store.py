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

from tea.core import paths, utils

# 路径解析统一由 core.paths 负责（$TEA_HOME > ~/.tea/ > CWD），这里只做别名转发，
# 让 config_store.HOME_ENV 这类既有引用保持可用。
CONFIG_ENV = paths.CONFIG_ENV
HOME_ENV = paths.HOME_ENV
CONFIG_NAME = paths.CONFIG_NAME

#: 全部数据源（顺序即优先级），DEFAULTS 与旧配置迁移共用一份
ALL_DATA_SOURCES = ["eastmoney", "tencent", "sina", "netease", "ifeng"]
#: 单源时代的重试次数：链上只有东财时靠死磕硬撑，迁移到多源后没必要
LEGACY_RETRIES = 4
#: 迁移提示（两行，用 \n 内嵌）。内网只放通东财域名的用户被自动推上五源反而更慢
#: （每次都要等东财超时再白试四家），所以第二行直接把退回单源的写法给出来。
MIGRATION_NOTICE = (
    "✓ 已自动启用 5 源降级（东财/腾讯/新浪/网易/凤凰），"
    "可在 tea_config.json 里调整 market.data_sources\n"
    "  如你的网络只允许访问东财域名，可将 market.data_sources 改回 [\"eastmoney\"]"
)

DEFAULTS: Dict[str, Any] = {
    "version": 1,
    # ---------------------------------------------------------- 元信息
    # initialized 是「首次启动向导」的唯一判据：配置文件不存在、或存在但这里仍是
    # False（老配置升级上来就是这种），都会在进主菜单前引导一次。
    "meta": {
        "initialized": False,
        "initialized_at": None,
        "wizard_version": 0,
        "wizard_skipped": False,
        # 单源→多源的一次性迁移标记，见 _migrate_v1_to_multisource
        "multisource_migrated": False,
    },
    # ---------------------------------------------------------- 路径
    "paths": {
        "data_dir": "data",
        "reports_dir": "reports",
        "logs_dir": "logs",   # 运行日志（每日轮转）
        "plan_file": "trade_plan.json",
        "daily_state_file": "daily_state.json",
        "capital_state_file": "capital_state.json",
        "watch_pool_file": "watch_pool.json",
        "trades_file": "trades.json",
        "seed_records_file": "seed_records.jsonl",
        "price_track_file": "price_track.json",
        "seed_trace_jsonl": "seed_trace.jsonl",
        "seed_trace_md": "SEED_TRACE.md",
        "accumulator_file": "accumulator.jsonl",
        "sector_cache_file": ".tea_sector_cache.json",
        # 无备源的东财接口（涨跌家数/涨停池）磁盘兜底缓存：实时取数失败时回退最近值。
        "breadth_cache_file": ".tea_breadth_cache.json",
        "ztpool_cache_file": ".tea_ztpool_cache.json",
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
        "member_max_pages": 15,    # 提高上限，捕获超大规模板块（如机械设备）的全部成员
        "member_fields": "f3,f8,f12,f14,f20",
        # 板块成分股就东财一家有，无家可降；而种子扫描要扫 30 个板块×多页。
        # 东财挂的时候每一环都死磕到顶，总耗时从几十秒满到几分钟，
        # 所以这一项的重试单独压得更低（反正拿不到就跳过这个板块）。
        "member_retries": 2,
        # 板块排名 / 涨跌家数是选股的根、且同样东财独家无备源。它们的重试要覆盖
        # 整条 cdn_hosts_quote 节点池（默认 3 个节点）：全局 retries=2 只试前两个
        # 节点，第三个（常是 push2delay 这类可用的）根本轮不到就回退磁盘兜底。
        "sector_retries": 3,
        "breadth_retries": 3,
        # 报价 / K 线是东财主源（push2 / push2his），同样要走节点池：报价池 3 个、
        # K 线池 4 个节点，全局 retries=2 只试前两个，健康节点排在后面就漏掉、
        # 被迫切到腾讯等备源。单独覆盖整条池，尽量在主源内命中。
        "quote_retries": 3,
        "kline_retries": 4,
        "breadth_fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "breadth_flat_eps": 0.05,
        "breadth_max_probes": 24,
        "ztpool_page_size": 300,
        "ztpool_fallback_days": 3,
        # ------------------------------------------------ 多数据源降级链
        # 顺序即优先级：前一家取不到才轮到后一家。默认全开五家——只留东财时
        # 降级链名存实亡，东财一抖整场扫描就是满屏「网络抖动」。
        # 网易只有报价、凤凰只有 K 线，链上会按方法自动跳过没有能力的那家。
        # 想退回单源就把这里改成 ["eastmoney"]（迁移只做一次，不会再被改回来）。
        "data_sources": list(ALL_DATA_SOURCES),
        # 每家每方法单独的超时（秒）。备源存在的意义是「快速接手」，
        # 沿用主源的 8s 会让一次三源降级拖到 24s+，所以备源一律给得更短。
        "provider_timeouts": {
            "eastmoney": {"quote": 6.0, "klines": 8.0, "index": 8.0},
            "tencent": {"quote": 4.0, "klines": 6.0, "index": 6.0},
            "sina": {"quote": 4.0, "klines": 6.0, "index": 6.0},
            "netease": {"quote": 3.0},
            "ifeng": {"klines": 5.0},
        },
        # 腾讯：报价用 `q=` 拼代码（sh600519），K 线走 appstock。
        "tencent_quote_url": "https://qt.gtimg.cn/q=",
        "tencent_kline_url": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        # 新浪：报价同样是 `list=` 拼代码。缺 Referer 会直接 403，不是 IP 被封。
        "sina_quote_url": "https://hq.sinajs.cn/list=",
        "sina_kline_url": "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
        "sina_referer": "https://finance.sina.com.cn",
        "sina_kline_scale": 240,   # 分钟；240 = 日线
        "sina_kline_max": 260,     # datalen 上限，取够一年日线
        # 网易：代码前缀沪 0 / 深 1（不是 sh/sz，也和东财 secid 相反）。
        "netease_quote_url": "http://api.money.126.net/data/feed/",
        # 凤凰：type=last 给最近一年多日线，接口不支持按条数裁剪。
        "ifeng_kline_url": "https://api.finance.ifeng.com/akdaily/",
        "ifeng_kline_type": "last",
        # 防封
        "timeout": 8.0,
        # 同一家源死磕的次数。后面还有四家备源，在第一家身上耗 4 次×3s 只是把
        # 降级往后拖 12 秒噪音，所以只重试 2 次就换下家。节点池仅影响 host 选择顺序
        # （配合 _preferred_host 记忆），尝试次数由 retries 严格控制。
        "retries": 2,
        "retry_backoff": 1.7,
        "delay_base": 0.35,
        "delay_spread": 0.25,
        "delay_after_error": 1.2,
        "rotate_referer": True,
        "rotate_ua": True,
        "rotate_cdn": True,
        "cdn_hosts_quote": ["push2.eastmoney.com", "82.push2.eastmoney.com", "push2delay.eastmoney.com"],
        "cdn_hosts_kline": ["push2his.eastmoney.com", "1.push2his.eastmoney.com",
                             "2.push2his.eastmoney.com", "7.push2his.eastmoney.com"],
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
        # 东财是国内站，不该走翻墙代理。默认忽略 shell 里的 http_proxy/https_proxy，
        # 否则代理连不上会 ProxyError 全崩。真需要代理取数（如境外）再置 True，
        # 或直接配 proxy_pool。
        "use_env_proxy": False,
        # 重试时在屏上出一声（一次超时 8s，不提示就像程序死了）
        "show_progress": True,
        # 重试提示的最小间隔（秒）：并发/密集重试时按此节流，避免刷屏。
        # 降级链自己会报「改用腾讯」，这行笼统提示可以更沉默些。
        "retry_notice_gap_sec": 5.0,
        # 请求耗时日志只留最近 N 条（内存内，不落盘）
        "request_log_cap": 200,
        # 缓存
        "quote_cache_sec": 20,
        "kline_cache_sec": 300,
        "sector_cache_sec": 600,
        "sector_disk_cache_hours": 24,
        "member_cache_sec": 600,
        "breadth_cache_sec": 120,
        "ztpool_cache_sec": 120,
        # 涨跌家数 / 涨停池磁盘兜底缓存的保留时长（小时）。这两个是东财独家、无备源，
        # 实时取数间歇性 RemoteDisconnected 时回退到最近一次成功值，避免天气里出现「—」。
        "breadth_disk_cache_hours": 6.0,
        "ztpool_disk_cache_hours": 6.0,
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
        # 板块排名门槛收紧 8→3：实证排名 1-3 板块 T+1 胜率 50%、T+5 均 +3.3%，
        # 而排名 6~15 板块 T+1 胜率仅 0~17%、T+5 均 -7%。2 分只给最强前三。
        # 1 分档同步收紧 15→5：与「只做前 5 板块」一致，不给中游板块（6~15）留口子。
        "sector_rank_full": 3,
        "sector_limit_up_full": 2,
        "sector_rank_half": 5,
        "sector_limit_up_half": 1,
        "sector_inner_top_pct": 0.10,
        "sector_inner_bonus": 1,
        "sector_inner_tail_pct": 0.50,
        "sector_inner_penalty": 1,
        "sector_dim_cap": 2,
        # ② 大盘趋势（分级共振扣分，范围 -1~+1，扣分封顶 -1）
        # 更稳的趋势定义：不用「现价是否高于 MA20」的单点二元，而是位置（带缓冲）
        # + 方向（MA20 斜率）两个信号合成 -2~+2 的态势，再映射成共振分的加减。
        # 缓冲带消掉贴线震荡的来回翻转；斜率看的是中期成本重心方向，比当日涨跌稳。
        # 扣分封顶 -1：弱势市满分 6 = 门槛 6，完美票仍有机会过，而不是被锁死。
        "index_dim_max": 1,
        "market_trend_bias_buffer": 0.3,
        "market_trend_slope_th": 0.2,
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
        # 与 strategy.min_odds 同步降到 2：否则「止损结构」维度恒为 0 分，
        # 有效满分从 9 掉到 8，共振门槛更难到。
        "sl_struct_min_odds": 2.0,
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
        "sector_rank_strong": 5,
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
        # 分时否决只在盘中交易时段生效：收盘/盘后扫描时「现价≈当日最高」是强势股
        # 常态，分时位置≈1.0 会把龙头误判成「追高/封顶」。盘前/午间/盘后跳过该否决
        # 并留痕（veto.intraday_skipped）。T+1 真实买入在 14:00–14:45 盘中会重新评估。
        "skip_intraday_check_off_session": True,
        # 分时高位软否决的强势豁免：放量上涨(chg>0 且 量比≥1.2 且 均线多头)的强势票
        # 贴日内高是强势确认，不按「追高」否决。招金黄金 08-21 分时 91% 被误杀 T+1 +7.56%。
        "intraday_strong_exempt": True,
        "limit_up_pct_base": 10.0,
        "soft_items": ["intraday_high", "bias_ma20", "near_limit_up", "chase_high"],
    },
    # ---------------------------------------------------------- 权限
    "permissions": {
        "main": True,
        "gem": True,
        # 科创板存在大量符合策略的标的，默认开启以避免系统性遗漏
        "star": True,
        "bse": False,
    },
    # ---------------------------------------------------------- 法/术：策略主段
    "strategy": {
        "max_position_pct": 0.50,
        "gray_ratio": 0.30,
        "confirm_ratio": 0.70,
        "pass_threshold": 6,
        # 胜率因子门槛（阶段 A 硬规则）：历史胜率低的特征强制降级观察，不参与可买。
        # 依据 62 条回填样本：板块排名 1-3 胜率 50%、6~15 仅 0~17%；突破阶段仅 6%。
        "winrate_gate_enabled": True,
        "winrate_sector_rank_buyable_max": 5,
        "winrate_breakout_sector_rank_max": 3,
        # R:R 门槛 3 → 2：止损硬顶 6%（atr_sl_hard_max_pct）+ 止盈上限 15%
        # （atr_tp_cap_pct）+ 双边滑点 0.5% 的结构下，含滑点最大盈亏比 ≈2.1，
        # 3 恒不可达 → 引擎结构性地出不了「可买」。降到 2 后 R:R 由「止盈抬升」
        # 逻辑自动满足（需 tp≈14.4% ≤ 15% 上限）。见 docs/CHANGELOG.md。
        "min_odds": 2,
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
        # 板块硬门槛排名上限，收紧 10→5：排名 6~15 的板块 T+1 胜率 0~17%，
        # 是当前 19% 总胜率的最大拖累，只做最强前 5 板块。
        "seed_min_sector_rank": 5,
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
        # 龙头 MA20 乖离软否决 20% → 25%：实证被 20%~26% 乖离否决的龙头
        # T+3/T+5 多数继续上涨（成都先导 +9.6%、博腾 +3.3%），20% 阈值偏严误杀。
        "veto_bias_leader_pct": 25.0,
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
        # 上证 MA20 下方不再硬拦（原 block_new_eval_when_index_below_ma20）：
        # 弱势市由共振「大盘趋势」维分级扣分表达，避免与评分重复计数。
        "cancel_high_score_min": 7,

        "defend_stance_bump": 1,
    },
    # ---------------------------------------------------------- 种子扫描细则
    "seed": {
        # 板块初筛池从 30 拓宽至 40，以覆盖排名靠后但仍可能产生候选的板块
        "sector_scan_topn": 40,
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
        "mild_chg_below_limit_up": 0.2,
        "mild_score_max": 100.0,
        "sector_relax_score": 60.0,
        # 涨停1家放宽通道的排名上限（原无上限，会放排名 9~15 的中游板块进池）。
        "sector_relax_rank": 5,
        # 无涨停通道：弱市好板块常无涨停，阈值从 70 下调到 65 扩容
        # 排名上限收紧 12→6：无涨停板块本就更弱，再放排名 7~12 的中游板块只会拉低胜率。
        "sector_relax_score_nozt": 65.0,
        "sector_relax_rank_nozt": 6,
        "diversify_replace_last": True,
        "shadow_bonus": 18.0,
        "shadow_near_rank": 6,
        "shadow_keep_days": 2,
        # 三档门槛
        "strict_min_chg": 3.0,
        "strict_max_chg": 5.5,
        # 涨幅窗下限动态化：最强板块 ≥5% 保持基准，4%~5% 下调 0.5，<4% 落到地板
        "dyn_min_chg_enabled": True,
        "dyn_min_chg_floor": 2.0,
        "dyn_strong_sector_chg": 5.0,
        "dyn_weak_sector_chg": 4.0,
        "strict_min_identity": 70,
        "strict_min_pick": 60,
        # 板块内排名比例上限，略微放宽以减少温和票因板块排名悬在半截而被过滤。
        # 原值 0.50 → 0.55（需结合实盘验证）。
        "strict_rank_pct": 0.55,
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
        "cap_min": 30.0,
        "cap_max": 300.0,
        "turnover_max": 20.0,
        "leader_pass_bonus": 1,
        "leader_pass_floor": 6,
        # 大盘趋势不再用硬闸（原 require_market_uptrend）：硬闸在贴线处会整个
        # 关死扫描、且与情绪/共振里的 MA20 信号重复计数。改为在 9 分共振的
        # 「大盘趋势」维做分级扣分（见 scoring.market_trend_*），弱势市更难凑满
        # 门槛但不至于一票否决全部候选。
        "near_miss_gap": 1,
        "max_watch_output": 3,
        "max_near_miss_output": 6,
        "eve_min_chg": 1.0,
        "eve_max_chg": 3.0,
        "eve_trigger_intraday": 0.75,
        # 低吸（启动前夕）板块池：追涨吃 TOP1-3 已涨停的鱼尾，低吸吃排名 3~10、
        # 刚开始升温（涨幅 2~4%、涨停≤1）的鱼身。仅观察落盘，暂不写计划。
        "lowbuy_rank_min": 3,
        "lowbuy_rank_max": 10,
        "lowbuy_chg_min": 2.0,
        "lowbuy_chg_max": 4.0,
        "lowbuy_limit_up_max": 1,
        "sprout_scan_enabled": True,
        "member_fetch_cap": 60,
        # 预审候选上限：板块池拓宽后需相应提高，防止有效候选被截断
        "candidate_fetch_cap": 80,
        # pick_score 系统分权重与分档（默认与历史硬编码一致）
        "pick": {
            "sector_position_weight": 35.0,
            "sector_rank_denom": 30.0,
            "inner_position_weight": 25.0,
            "inner_default_pct": 0.6,
            "chg_weight": 20.0,
            "chg_penalty_per_pct": 5.0,
            "turnover_none_score": 5.0,
            "turnover_score_brackets": [
                {"min": 3.0, "max": 10.0, "score": 10.0},
                {"min": 2.0, "max": 15.0, "score": 6.0},
                {"min": 0.0, "max": 100.0, "score": 3.0},
            ],
            "cap_none_score": 5.0,
            # 首个命中生效（_bracket_score），所以档位顺序即优先级：
            # 80-200 亿→10 分；50-80 亿走第二档→8 分；30-50 亿（cap_min 下调到 30
            # 亿后新增的区间）→6 分，不再直接掉到兜底档；<30 或 >300 亿兜底 4 分。
            "cap_score_brackets": [
                {"min": 80.0, "max": 200.0, "score": 10.0},
                {"min": 50.0, "max": 300.0, "score": 8.0},
                {"min": 30.0, "max": 50.0, "score": 6.0},
                {"min": 0.0, "max": 1000.0, "score": 4.0},
            ],
        },
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
        # 样本不足时不再抬升通过门槛（1 → 0）：当前零实盘样本，这个 +1 会让
        # 共振门槛无依据地 +1，进一步收紧本就过严的准入。样本积累后再评估是否恢复。
        "insufficient_pass_bump": 0,
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
    # ---------------------------------------------------------- 运行日志
    # 与 data/ 下的结构化追溯互补：这里记「程序运行过程」，按日切割到 logs/ 目录。
    "logs": {
        # 历史日志保留天数（TimedRotatingFileHandler 的 backupCount）
        "backup_days": 30,
        # 日志级别：DEBUG / INFO / WARNING / ERROR
        "level": "INFO",
    },
    # ---------------------------------------------------------- 交互
    "ui": {
        "lang": "zh",
        "color": True,
        "confirm_buy": True,
        "show_debug": False,
    },
}


def _migrate_v1_to_multisource(cfg_data: dict) -> "tuple[dict, bool]":
    """一次性迁移：单源时代的配置升级到全 5 源降级链。

    只改 DEFAULTS 治不了已经落盘的 tea_config.json：save() 写的是全量配置，
    当时的 `data_sources: ["eastmoney"]`（或根本没这一项）会把用户永久钉在单源上，
    降级链就只包了东财一家、名存实亡。

    迁移幂等：`meta.multisource_migrated` 一旦置位就不再看第二眼，所以之后用户
    自己改回 ["eastmoney"] 也不会被反复升级。返回（配置, 是否真的改了东西）。
    """
    meta = cfg_data.setdefault("meta", {})
    if meta.get("multisource_migrated"):
        return cfg_data, False
    market = cfg_data.setdefault("market", {})
    meta["multisource_migrated"] = True
    sources = market.get("data_sources")
    if sources is not None and list(sources) != ["eastmoney"]:
        return cfg_data, False       # 用户显式配了其他组合，尊重
    market["data_sources"] = list(ALL_DATA_SOURCES)
    # 同一源死磕 4 次的意义随降级链消失：换下家比在原地等快。只改“还是旧
    # 默认值”的情形，用户自己调过的重试次数不动。
    if market.get("retries") in (None, LEGACY_RETRIES):
        market["retries"] = DEFAULTS["market"]["retries"]
    return cfg_data, True


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
    """运行时基准目录。打包版落在 ~/.tea/，源码版仍是 CWD。"""
    return str(paths.data_dir())


def config_path() -> str:
    return str(paths.config_path())


class Config:
    """配置容器：点号路径读写 + 原子持久化。"""

    def __init__(self, data: Optional[dict] = None, path: Optional[str] = None):
        self.path = path or config_path()
        self.data = _deep_merge(DEFAULTS, data or {})
        self._merge_cdn_pools()

    def _merge_cdn_pools(self) -> None:
        """把 DEFAULTS 里的 CDN 节点并进用户配置（默认在前，用户额外节点接后）。

        save() 落盘的是全量配置，老配置会把当时的节点列表钉死，新增的官方镜像
        节点就进不来。节点池是纯兜底选项，只多不少，因此始终 union 保证池子的改进
        能覆盖到已有配置的用户。用户自定义的额外节点不会丢。
        """
        mk = self.data.setdefault("market", {})
        for pool in ("cdn_hosts_quote", "cdn_hosts_kline"):
            base = list((DEFAULTS.get("market") or {}).get(pool) or [])
            cur = list(mk.get(pool) or [])
            mk[pool] = base + [h for h in cur if h not in base]

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

    def logs_dir(self) -> str:
        """日志存放目录（绝对路径）。"""
        d = self.get("paths.logs_dir", "logs")
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
    """加载配置（进程内缓存）。旧配置顶多被迁移一次并回写。"""
    global _CACHED
    if _CACHED is not None and not reload and path is None:
        return _CACHED
    p = path or config_path()
    raw = utils.read_json(p, default={}) or {}
    # 打包版首次启动：~/.tea/ 里还没有配置，就拿随包模板做种子。没带模板
    # （默认就不带，见 packaging/build.py --bundle-config）则照旧路走 DEFAULTS。
    if not raw and paths.is_frozen():
        tmpl = paths.bundled_config()
        if tmpl is not None:
            raw = utils.read_json(str(tmpl), default={}) or {}
    # 没有配置文件就无从迁移：新用户直接拿 DEFAULTS（已经是 5 源），
    # 也不应该在首次启动向导之前就悄悄写出一份配置。
    if raw and os.path.exists(p):
        raw, changed = _migrate_v1_to_multisource(raw)
    else:
        changed = False
    cfg = Config(raw, path=p)
    if changed:
        cfg.save()
        # 逐行打：直接 print 一个序列会把括号与引号也摆到用户眼前。
        for line in MIGRATION_NOTICE.split("\n"):
            print(line, flush=True)
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
