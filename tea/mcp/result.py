from __future__ import annotations

from dataclasses import dataclass

from tea.mcp.sanitize import strip_ansi, truncate


@dataclass
class TeaResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    command: list[str]

    @property
    def text(self) -> str:
        out = (self.stdout or "").strip()
        err = (self.stderr or "").strip()
        if self.ok:
            return out or "（无输出）"
        if err:
            return f"TEA 调用失败 (exit {self.exit_code}): {err[:500]}"
        return f"TEA 调用失败 (exit {self.exit_code}): {out[:500] or '未知错误'}"

    def normalized(self) -> TeaResult:
        return TeaResult(
            ok=self.ok,
            stdout=truncate(strip_ansi(self.stdout)),
            stderr=strip_ansi(self.stderr),
            exit_code=self.exit_code,
            command=list(self.command),
        )
