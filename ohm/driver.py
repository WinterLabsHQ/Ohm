"""Driver — orchestrates the pass pipeline (compiler design §1, §7).

Checking passes run to completion (all errors at once); P-LOWER/emit never run
if any diagnostic is an error. No output is ever emitted from an errored design.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from ohm import ir
from ohm.check_bringup import check_bringup
from ohm.check_intent import check_intent
from ohm.check_kinds import check_kinds
from ohm.check_mfg import check_mfg
from ohm.check_quant import check_quant
from ohm.check_structural import check_structural
from ohm.check_units import annotate_units, check_units
from ohm.closedform import fold_asserts
from ohm.diagnostics import Diagnostic, has_errors
from ohm.holes import collect_holes
from ohm.loader import load
from ohm.lower import lower
from ohm.resolve import resolve

CHECKERS = [check_units, check_structural, check_kinds, check_intent, fold_asserts,
            check_quant, check_bringup, check_mfg]


@dataclass
class CompileResult:
    design: ir.Design | None = None  # post-annotate_units (typed)
    design_high: ir.Design | None = None  # pre-annotation (high)
    low: ir.IRLow | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not has_errors(self.diagnostics)


def compile_source(source: str, path: str = "<string>", *, emit: bool = True,
                   debug_passes: bool = False) -> CompileResult:
    result = CompileResult()
    program, pdiags = load(source, path)  # parse + `use` import closure (ADR-007)
    result.diagnostics += pdiags
    # parse errors are not fatal to analysis: resolve + checkers still run on the
    # recovered partial Program (design §5 best-effort); lower/emit never runs.
    design, rdiags = resolve(program, path)
    result.diagnostics += rdiags
    result.design_high = design
    design, udiags = annotate_units(design, path)
    result.diagnostics += udiags
    result.design = design
    # lower ONCE (pure; safe on poisoned/partial designs) and share the result
    # with check_intent/check_quant and the emit path.
    low, ldiags = lower(design, path)
    snapshot = ir.to_json(design) if debug_passes else None
    for checker in CHECKERS:
        extra = {"low": low} if checker in (check_intent, check_quant, check_bringup,
                                            check_mfg) else {}
        result.diagnostics += checker(design, path, **extra)
        if debug_passes and ir.to_json(design) != snapshot:
            raise AssertionError(f"checker '{checker.__name__}' mutated the design")
    result.diagnostics += ldiags  # lower's I007 warnings, collected exactly once
    if emit and result.ok:
        result.low = dataclasses.replace(low, holes=collect_holes(design))
    return result
