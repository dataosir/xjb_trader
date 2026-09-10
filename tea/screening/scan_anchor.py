"""扫描锚点（scan_anchor）：主样本幂等维度。

同一交易日可能多次 seed-plan（早盘手动 / 14:30 自动 / 漏扫 --force）。
``seed_records`` 已按 (date, code) 去重，但首扫快照、scan_details、accumulator
仍会被非锚点扫描污染。本模块定义锚点优先级与「是否覆盖」规则。

锚点（高 → 低）：
  primary  — launchd 14:30 官方扫描（TEA_LAUNCHD=1）
  manual   — tea seed-plan --force 漏扫补救
  winrate  — 胜率选股（影子通道，不写 scan_details 主文件）
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

ANCHOR_PRIMARY = "primary"
ANCHOR_MANUAL = "manual"
ANCHOR_WINRATE = "winrate"

_RANK = {
    ANCHOR_PRIMARY: 3,
    ANCHOR_MANUAL: 2,
    ANCHOR_WINRATE: 1,
}


def resolve_seed_anchor() -> str:
    """当前 seed-plan 进程的锚点（launchd → primary，否则 manual）。"""
    if os.environ.get("TEA_LAUNCHD") == "1":
        return ANCHOR_PRIMARY
    return ANCHOR_MANUAL


def anchor_rank(anchor: Optional[str]) -> int:
    """未知/历史无字段 → 视作 manual。"""
    if not anchor:
        return _RANK[ANCHOR_MANUAL]
    return _RANK.get(anchor, _RANK[ANCHOR_MANUAL])


def scan_richness(result: Optional[dict]) -> int:
    """扫描结果「信息量」评分，用于避免空扫覆盖有效 scan_details。"""
    if not result:
        return 0
    n = 0
    n += len(result.get("candidates") or [])
    n += len(result.get("buyable") or []) * 10
    n += len(result.get("watch") or []) * 3
    n += len(result.get("eve") or []) * 3
    n += len(result.get("near_miss") or [])
    n += int(result.get("candidates_n") or 0)
    n += int(result.get("veto_passed_n") or 0)
    return n


def should_write_canonical_scan_details(
    new_result: dict,
    existing: Optional[dict],
    new_anchor: str,
) -> bool:
    """是否写入当日主 scan_details_{date}.json（非 sidecar）。"""
    if new_anchor == ANCHOR_WINRATE:
        return False
    if not existing:
        return True
    ex_anchor = existing.get("scan_anchor") or ANCHOR_MANUAL
    new_r = anchor_rank(new_anchor)
    ex_r = anchor_rank(ex_anchor)
    if new_r > ex_r:
        return True
    if new_r < ex_r:
        return False
    return scan_richness(new_result) >= scan_richness(existing)


def sidecar_scan_details_name(scan_date: str, scan_anchor: str, scan_id: str) -> str:
    """非主锚点或降级扫描的 sidecar 文件名（不含目录）。"""
    sid = (scan_id or "unknown").replace(":", "").replace(" ", "")[-12:]
    return f"scan_details_{scan_date}_{scan_anchor}_{sid}.json"
