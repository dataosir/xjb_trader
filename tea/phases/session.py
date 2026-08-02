"""跨阶段状态容器。

一次评估的全过程状态都挂在 Session 上，四个阶段依次往里填字段，
最后由 report.render_md 消费 `to_ctx()`。阶段之间不互相调用，只通过它传递。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import utils
from ..config_store import Config, load_config
from ..data import Market
from ..timing import Timing
from .prompt import IO


class Session:
    """一次评估的全过程状态容器（最终由 report.render_md 消费）。"""

    def __init__(self, cfg: Optional[Config] = None, market: Optional[Market] = None,
                 io: Optional[IO] = None, sent: Optional[dict] = None,
                 force: bool = False, capital: Optional[float] = None):
        self.cfg = cfg or load_config()
        self.mk = market or Market(self.cfg)
        self.io = io or IO()
        self.tm = Timing(self.cfg)
        self.sent = sent
        self.force = bool(force)

        self.code: Optional[str] = None
        self.name: Optional[str] = None
        self.quote: Optional[dict] = None
        self.ind: Optional[dict] = None
        self.sector: Optional[dict] = None
        self.identity: Optional[dict] = None
        self.intraday: Optional[float] = None
        self.stage: Optional[dict] = None
        self.veto: Optional[dict] = None
        self.sl_pct: Optional[float] = None
        self.tp_pct: Optional[float] = None
        self.has_news: bool = False

        self.ev: Optional[dict] = None
        self.expectancy: Optional[dict] = None
        self.followthrough: Optional[dict] = None
        self.mults: Dict[str, Any] = {}
        self.sizing: Optional[dict] = None

        self.session_gate: Optional[dict] = None
        self.code_gate: Optional[dict] = None
        self.buy_gate: Optional[dict] = None
        self.plan_item: Optional[dict] = None

        self.capital = capital
        self.available: Optional[float] = None
        self.decision: Optional[str] = None
        self.blocks: List[str] = []
        self.notes: List[str] = []
        self.phase_log: List[str] = []
        self.report_path: Optional[str] = None
        self.at = utils.now().strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------------------------- 记录
    def log(self, text: str) -> None:
        self.phase_log.append(text)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def block(self, text: str) -> None:
        self.blocks.append(text)

    def banner(self, title: str) -> None:
        self.io.say("")
        self.io.say(f"┌─ {title} " + "─" * max(0, 44 - len(title)))

    # -------------------------------------------------- 导出
    def to_ctx(self) -> dict:
        return {
            "at": self.at, "code": self.code, "name": self.name,
            "decision": self.decision, "force": self.force,
            "sentiment": self.sent,
            "session_gate": self.session_gate, "code_gate": self.code_gate,
            "buy_gate": self.buy_gate, "plan_item": self.plan_item,
            "ev": self.ev or {}, "expectancy": self.expectancy,
            "followthrough": self.followthrough, "mults": self.mults,
            "sizing": self.sizing, "capital": self.capital, "available": self.available,
            "blocks": self.blocks, "notes": self.notes, "phase_log": self.phase_log,
            "report_path": self.report_path,
        }
