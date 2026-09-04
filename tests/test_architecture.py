"""The shape of the compiler, as a gate rather than as a paragraph.

`AGENTS.md`, `docs/blueprint/OHM_COMPILER_DESIGN.md` and every index in
`docs/ci/` describe the same spine —

    load -> resolve -> annotate_units -> lower(once) -> checkers -> collect_holes -> emit
    AST  -> IR-HIGH -> IR-TYPED       -> IR-LOW

— and three invariants: hermetic, deterministic, analysis never mutates. Two of
those three already have gates (`tests/test_lower_emit.py` walks the build path
for forbidden imports; `tests/test_determinism.py` and `tools/emit_manifest.py`
measure the bytes). The SHAPE had none. It was prose, and prose does not fail.

What that cost, measured rather than supposed. Three pairs of modules had grown
into mutual dependencies, every one of them held apart only by an import written
inside a function body rather than at the top of the file:

    check_placement  <-> emit_kicad     (`_extent`, pure geometry, in the checker)
    emit_kicad       <-> emit_netlist   (`_source_id` one way, `_value_text` back)
    import_symbol    <-> provenance     (the digest, and the symbol reader)

The first two are fixed — `_extent` moved to the module that owns the placement
geometry, and the vocabulary every emitter shares moved to `ohm/emit_common.py`.
The third is declared below, with the reason it is the shape of the relationship
rather than an accident. A cycle that survives only because of where an import
is written comes back the first time somebody tidies the imports.
"""

from __future__ import annotations

import ast
from pathlib import Path

OHM = Path(__file__).resolve().parent.parent / "ohm"


def _imports(py: Path) -> set[str]:
    """Every `ohm.<module>` this file imports, wherever the statement sits.

    Function-local imports count. Hiding an edge inside a function body does not
    make it not an edge — it makes it an edge nobody can see, which is exactly
    how the two cycles this module now forbids were able to form.
    """
    out: set[str] = set()
    for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "ohm" and len(parts) > 1:
                out.add(parts[1])
    return out


def _graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for py in sorted(OHM.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        mod = py.stem if py.parent == OHM else py.parent.name
        graph.setdefault(mod, set()).update(_imports(py) - {mod})
    return graph


# The one mutual dependency that is the shape of the relationship rather than an
# accident, and therefore allowed. `provenance` owns what a pinout digest IS;
# `import_symbol` owns how a KiCad symbol library is READ. Each genuinely needs a
# piece of the other: `import_symbol` stamps a digest into the Ohm source it is
# generating (before any IR exists, which is why `digest_pairs` takes plain
# pairs), and `provenance.verify_against_libs` re-reads real libraries through
# `import_symbol`'s parser to diff them. Both directions are documented at their
# call sites. Splitting them would mean a third module holding one hash function.
ALLOWED_CYCLES = {("import_symbol", "provenance")}


def _cycles(graph: dict[str, set[str]]) -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    stack: list[str] = []
    seen: set[str] = set()

    def walk(mod: str) -> None:
        if mod in stack:
            found.add(tuple(sorted(set(stack[stack.index(mod):]))))
            return
        if mod in seen:
            return
        seen.add(mod)
        stack.append(mod)
        for target in sorted(graph.get(mod, ())):
            walk(target)
        stack.pop()

    for mod in sorted(graph):
        seen, stack = set(), []
        walk(mod)
    return found


def test_no_import_cycles_beyond_the_one_that_is_declared():
    """A cycle held apart by an import's position is not held apart."""
    extra = _cycles(_graph()) - ALLOWED_CYCLES
    assert not extra, (
        "new import cycle(s) in ohm/: "
        + "; ".join(" <-> ".join(c) for c in sorted(extra))
        + ". Move the shared thing DOWN to a module both can depend on — that is "
          "what ohm/emit_common.py is — rather than making one import reach back "
          "up. If the cycle really is the shape of the relationship, add it to "
          "ALLOWED_CYCLES with the reason, the way import_symbol/provenance is")


def test_the_pass_spine_is_the_one_the_documents_describe():
    """`driver.CHECKERS`, in order, and the stages around it.

    Every document that describes Ohm describes this list. When one of them says
    "8 checkers" and the list holds nine, the document is wrong and nothing says
    so — `docs/ci/README.md` said exactly that for a whole round.
    """
    from ohm import driver

    assert [c.__name__ for c in driver.CHECKERS] == [
        "check_units", "check_structural", "check_kinds", "check_intent",
        "fold_asserts", "check_quant", "check_bringup", "check_mfg",
        "check_placement",
    ], "the checker list changed; every document naming its length must follow"


def test_shared_does_not_depend_on_the_compiler():
    """`shared/` is the seam between the open compiler and the proprietary
    platform, so it may not import from `ohm/` — that would make the seam depend
    on the thing it exists to be independent of.

    `ohm/formats.py` states this and imports SCHEMA_VERSION in the permitted
    direction because of it. Nothing enforced the other direction.
    """
    root = Path(__file__).resolve().parent.parent / "shared"
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        assert not _imports(py), \
            f"shared/{py.name} imports from ohm/: {sorted(_imports(py))}"


def test_analysis_never_mutates_the_design():
    """Invariant 3, run rather than asserted.

    `compile_source(debug_passes=True)` re-serialises the design after every
    checker and raises if one changed it. This runs that over the whole corpus,
    so the invariant is measured on real designs and not only on the one a
    developer happened to try.
    """
    from ohm.driver import compile_source

    designs = sorted((OHM.parent / "tests" / "golden").glob("*.ohm"))
    designs += sorted((OHM.parent / "examples").glob("*.ohm"))
    assert len(designs) > 8, "corpus went missing"
    for design in designs:
        compile_source(design.read_text(encoding="utf-8"), design.name,
                       debug_passes=True)  # raises AssertionError on mutation
