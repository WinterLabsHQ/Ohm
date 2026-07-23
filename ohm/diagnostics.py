"""Diagnostics — a product surface (compiler design §5).

One record, two renderings: a terse machine-parseable line for agent loops and a
human block with source excerpt + caret + help. Codes are stable; severities gate
the driver, never the passes themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Span:
    line: int  # 1-based
    col: int  # 1-based
    end_line: int
    end_col: int


@dataclass(frozen=True)
class Diagnostic:
    severity: str  # "error" | "warning" | "info"
    code: str  # stable, e.g. "P001", "I014"
    message: str
    span: Span
    file: str = "<string>"
    help: str | None = None
    related: tuple[Span, ...] = field(default=())

    def machine(self) -> str:
        return f"{self.file}:{self.span.line}:{self.span.col}: {self.severity}[{self.code}]: {self.message}"

    def human(self, source: str | None = None) -> str:
        lines = [self.machine()]
        if source is not None:
            src_lines = source.splitlines()
            if 1 <= self.span.line <= len(src_lines):
                excerpt = src_lines[self.span.line - 1]
                lines.append(f"    {excerpt}")
                width = max(1, (self.end_col - self.col) if self.span.end_line == self.span.line else 1)
                lines.append("    " + " " * (self.span.col - 1) + "^" * width)
        if self.help:
            lines.append(f"    help: {self.help}")
        return "\n".join(lines)

    # convenience passthroughs used by human()
    @property
    def col(self) -> int:
        return self.span.col

    @property
    def end_col(self) -> int:
        return self.span.end_col


def has_errors(diags: list[Diagnostic]) -> bool:
    return any(d.severity == "error" for d in diags)
