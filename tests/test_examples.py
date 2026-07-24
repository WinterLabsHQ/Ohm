"""Top-level examples/ must always compile clean — the showcase never rots."""

from pathlib import Path

import pytest

from ohm.driver import compile_source

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.ohm"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_compiles_clean(path):
    r = compile_source(path.read_text(), str(path))
    assert r.ok, [d.machine() for d in r.diagnostics]
    noisy = [d for d in r.diagnostics if d.severity in ("error", "warning")]
    assert not noisy, [d.machine() for d in noisy]


def test_examples_exist():
    assert len(EXAMPLES) >= 5
