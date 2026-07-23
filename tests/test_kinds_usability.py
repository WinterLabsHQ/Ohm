"""Milestone K2: live-agent usability of the `kind` system (ADR-008). A fresh LLM
given only the kind syntax authored tests/examples/kind_demo_power_amp.ohm — a
self-declared OPA333 `(OpAmp)`, a multi-kind `(MOSFET, ThermalPad)` power FET, and
a USER-DEFINED `kind QuadratureEncoder` extending the taxonomy. It got every kind
declaration right first try (the compiler accepted all conformance); only the
package catalog name and module wiring syntax needed one correction round. Frozen."""

from pathlib import Path

from ohm.driver import compile_source

FIXTURE = Path(__file__).parent / "examples" / "kind_demo_power_amp.ohm"


def _result():
    return compile_source(FIXTURE.read_text(), str(FIXTURE))


def test_kind_demo_compiles_clean():
    result = _result()
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert not [d for d in result.diagnostics if d.severity == "error"]


def test_self_declared_opamp_conforms():
    (u,) = [c for c in _result().design.components if c.name == "OPA333"]
    assert u.kinds == ("OpAmp",)
    assert {p.satisfies for p in u.pins if p.satisfies} == {"out", "vneg", "inp", "inn", "vpos"}


def test_multi_kind_mosfet_thermalpad():
    (q,) = [c for c in _result().design.components if c.name == "PWRFET"]
    assert set(q.kinds) == {"MOSFET", "ThermalPad"}
    # the union of both contracts is satisfied (gate/drain/source + ep)
    assert {p.satisfies for p in q.pins if p.satisfies} == {"gate", "drain", "source", "ep"}


def test_user_defined_kind_extends_taxonomy():
    design = _result().design
    assert any(k.name == "QuadratureEncoder" for k in design.kinds)  # authored, not from std
    (e,) = [c for c in design.components if c.name == "MyEncoder"]
    assert e.kinds == ("QuadratureEncoder",)
