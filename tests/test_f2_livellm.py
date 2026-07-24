"""Milestone F, wave F2 (deferred item, now closed): live-LLM diagnostic loop.

A fresh LLM given only STYLE.md excerpts authored an I2C I/O-expander board.
First attempt: invented Power member names (R003 ×3) and — the seeded target —
omitted the I2C pull-ups, which **B001 caught** (the guide never mentioned
pull-ups; the *compiler* taught the requirement). One round of machine
diagnostics produced this clean board: correct members, 4.7k pulls to the
3.3V rail, open-drain pins modeled accurately. Frozen fixture."""

from pathlib import Path

from ohm.driver import compile_source

FIXTURE = Path(__file__).parent / "examples" / "io_expander_board.ohm"


def test_live_llm_board_is_clean():
    result = compile_source(FIXTURE.read_text(), str(FIXTURE))
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert not [d for d in result.diagnostics if d.severity in ("error", "warning")]


def test_pullups_present_on_i2c_nets():
    result = compile_source(FIXTURE.read_text(), str(FIXTURE))
    nets = {n.name: n for n in result.low.nets}
    sda = next(n for name, n in nets.items() if name.endswith("sda"))
    assert any(node.part_ref == "rsda" for node in sda.nodes)  # the taught fix


def test_open_drain_modeled():
    result = compile_source(FIXTURE.read_text(), str(FIXTURE))
    (exp,) = [c for c in result.design.components if c.name == "PCA9536"]
    roles = {p.name: p.role for p in exp.pins}
    assert roles["SDA"] == "open_collector" and roles["SCL"] == "open_collector"
