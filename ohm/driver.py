"""Driver — orchestrates the pass pipeline (compiler design §1, §7).

Checking passes run to completion (all errors at once); P-LOWER/emit never run
if any diagnostic is an error. No output is ever emitted from an errored design.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from ohm import ir
from ohm.check_intent import check_intent
from ohm.check_quant import check_quant
from ohm.check_structural import check_structural
from ohm.check_units import annotate_units, check_units
from ohm.closedform import fold_asserts
from ohm.diagnostics import Diagnostic, has_errors
from ohm.holes import collect_holes
from ohm.lower import lower
from ohm.parse import parse
from ohm.resolve import resolve

CHECKERS = [check_units, check_structural, check_intent, fold_asserts, check_quant]


@dataclass
class CompileResult:
    design: ir.Design | None = None  # post-annotate_units (typed)
    design_high: ir.Design | None = None  # pre-annotation (high)
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
    result.design_high = design
    design, udiags = annotate_units(design, path)
    result.diagnostics += udiags
    result.design = design
    for checker in CHECKERS:
        result.diagnostics += checker(design, path)
    if emit and result.ok:
        low, ldiags = lower(design, path)
        result.low = dataclasses.replace(low, holes=collect_holes(design))
        result.diagnostics += ldiags
    return result
