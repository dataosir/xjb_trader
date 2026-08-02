"""四阶段交互流程。

    Phase1 标的锁定   代码门禁 → 行情 → 板块/身份 → VETO → 止损止盈
    Phase2 数学计算   完整评估（含滑点 R:R）+ 初步仓位
    Phase3 评分       9 分共振 + 动态门槛
    Phase4 准入       三道门禁复核 → 决策 → 建仓

支撑模块：

    results   四阶段共用的返回值哨兵（OK / REJECT / ABORT）
    prompt    交互抽象 IO（真人输入 / 预设答案 / 静默）
    session   跨阶段状态容器 Session

本文件只做再导出，不放实现——否则 `from .phases import phase1` 会顺带
加载状态容器与交互层，依赖关系也看不出方向。
"""
from __future__ import annotations

from . import phase1, phase2, phase3, phase4, results
from .prompt import IO
from .results import ABORT, OK, REJECT
from .session import Session

__all__ = [
    "IO",
    "Session",
    "results",
    "OK",
    "REJECT",
    "ABORT",
    "phase1",
    "phase2",
    "phase3",
    "phase4",
]
