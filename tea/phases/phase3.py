"""Phase3 9 分共振评分展示：把 Phase2 已算出的六维明细摊开给人看。

评分本身在 preflight.score_nine 里完成（单一实现，避免公式分叉），
这一阶段只负责呈现与门槛对比。
"""
from __future__ import annotations

from tea.screening import preflight
from .results import OK
from .session import Session

def run(s: Session) -> str:
    io = s.io
    s.banner("Phase 3 · 9 分共振评分")
    ev = s.ev or {}
    io.say(preflight.format_scoring(ev))

    total, th = ev.get("total_score"), ev.get("pass_threshold")
    gap = ev.get("gap")
    if total is not None and th is not None:
        if total >= th:
            io.say(f"  ✓ 共振分 {total} ≥ 有效门槛 {th}")
        else:
            io.say(f"  ✗ 共振分 {total} < 有效门槛 {th}（差 {gap} 分）")
            if gap == 1:
                io.say("    差 1 分 → 建议纳入启动待定轨，等回踩/补分再评，不要凑数买入")
    s.log(f"Phase3 完成：共振 {total}/{th}")
    return OK
