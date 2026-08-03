"""交互抽象。

同一套阶段代码要能跑三种场景：真人在 CLI 上敲键盘、runner 用预设答案跑无人值守、
自测用固定答案跑断言。所以把"问一句话拿一个值"收成 IO 这一个类，
阶段代码只依赖它，不直接调用 input()/print()。

模块名用 prompt 而非 io，避免与标准库 io 同名造成阅读歧义。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tea.core import utils


class IO:
    """交互抽象：answers 预设答案（键 → 值）时不阻塞等待输入。"""

    def __init__(self, answers: Optional[Dict[str, Any]] = None, quiet: bool = False,
                 interactive: bool = True):
        self.answers = dict(answers or {})
        self.quiet = quiet
        self.interactive = interactive
        self.transcript: List[str] = []

    def say(self, text: str = "") -> None:
        self.transcript.append(text)
        if not self.quiet:
            print(text)

    def _preset(self, key: Optional[str]) -> Any:
        if key and key in self.answers:
            return self.answers[key]
        return None

    def ask(self, prompt: str, key: Optional[str] = None,
            default: Optional[str] = None) -> Optional[str]:
        """返回字符串；非交互且无预设时返回 default。"""
        pre = self._preset(key)
        if pre is not None:
            self.say(f"{prompt} {pre}")
            return str(pre)
        if not self.interactive:
            return default
        hint = f"（默认 {default}）" if default is not None else ""
        try:
            raw = input(f"{prompt}{hint}：").strip()
        except (EOFError, KeyboardInterrupt):
            self.say("")
            return None
        return raw or default

    def ask_float(self, prompt: str, key: Optional[str] = None,
                  default: Optional[float] = None,
                  lo: Optional[float] = None, hi: Optional[float] = None) -> Optional[float]:
        for _ in range(3):
            raw = self.ask(prompt, key, None if default is None else utils.num(default))
            if raw is None or raw == "":
                return default
            v = utils.to_float(raw)
            if v is None:
                self.say("  输入无效，请输入数字")
                if not self.interactive:
                    return default
                continue
            if lo is not None and v < lo:
                self.say(f"  不得小于 {lo}")
                if not self.interactive:
                    return default
                continue
            if hi is not None and v > hi:
                self.say(f"  不得大于 {hi}")
                if not self.interactive:
                    return default
                continue
            return v
        return default

    def ask_yes(self, prompt: str, key: Optional[str] = None, default: bool = False) -> bool:
        raw = self.ask(f"{prompt} [y/N]", key, "y" if default else "n")
        return str(raw or "").strip().lower() in ("y", "yes", "1", "true", "是")

    def ask_force(self, prompt: str = "输入 FORCE 强制继续（其他任意键放弃）",
                  key: str = "force") -> bool:
        raw = self.ask(prompt, key, "")
        return str(raw or "").strip().upper() == "FORCE"
