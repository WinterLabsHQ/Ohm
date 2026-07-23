"""Driver — orchestrates the pass pipeline (compiler design §1, §7).

Checking passes run to completion (all errors at once); P-LOWER/emit never run
if any diagnostic is an error. No output is ever emitted from an errored design.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ohm import ir
from ohm.check_units import annotate_units, check_units
from ohm.diagnostics import Diagnostic, has_errors
from ohm.lower import lower
from ohm.parse import parse
from ohm.resolve import resolve

# checker registry — Tier-S (P5) and Tier-I (P6) append here
CHECKERS = [check_units]


@dataclass
class CompileResult:
    design: ir.Design | None = None
    low: ir.IRLow | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not has_errors(self.diagnostics)


def compile_source(source: str, path: str = "<string>", *, emit: bool = True) -> CompileResult:
    result = CompileResult()
    program, pdiags = parse(source, path)
    result.diagnostics += pdiags
    if has_errors(pdiags):
        return result  # fatal boundary: no analysis on unparsed source
    design, rdiags = resolve(program, path)
    result.diagnostics += rdiags
    design, udiags = annotate_units(design, path)
    result.diagnostics += udiags
    result.design = design
    for checker in CHECKERS:
        result.diagnostics += checker(design, path)
    if emit and result.ok:
        result.low, ldiags = lower(design, path)
        result.diagnostics += ldiags
    return result
