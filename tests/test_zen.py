"""P7 gate: a real-shaped .zen file imports to IR and re-exports; round-trips
structurally. Interop only — a test also proves `ohm build` never touches it."""

from pathlib import Path

from ohm.driver import compile_source
from ohm.emit_zen import _refname, emit_zen
from ohm.imp_zen import import_zen

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
ZEN_SRC = (FIXTURES / "led_indicator.zen").read_text()


def structure(low, rename=None):
    """Structural fingerprint: parts + net partition of (ref, pin) endpoints.

    `rename` maps Ohm refs to the names the export had to use. Ohm refs are
    hierarchical paths (`mcu.u`); diodeinc/pcb 0.4.41 refuses a Component whose
    name contains a dot, so `emit_zen` substitutes underscores and the round trip
    is an equality under that renaming rather than a literal one. `_refname`
    uniquifies collisions, so the renaming is a bijection and nothing about the
    strength of the comparison changes: the same parts, the same net partition,
    a different spelling of the labels.
    """
    ref = (lambda r: r) if rename is None else (lambda r: rename.get(r, r))
    parts = {(ref(p.ref), p.package if isinstance(p.package, str) else "")
             for p in low.parts}
    nets = {frozenset((ref(n.part_ref), n.pin) for n in net.nodes)
            for net in low.nets if net.nodes}
    return parts, nets


def zen_refs(low):
    """The ref -> Zener-instance-name map `emit_zen` will produce for `low`.

    Rebuilt with the same function in the same order the exporter walks it, so this
    test cannot drift away from the exporter by agreeing on a stale spelling."""
    taken = {}
    return {p.ref: _refname(p.ref, taken) for p in low.parts}


# ---------- gate: import a real-shaped .zen ----------
def test_import_led_indicator():
    low, diags = import_zen(ZEN_SRC, "led_indicator.zen")
    assert [d for d in diags if d.severity == "error"] == []
    assert {(p.ref, p.component) for p in low.parts} == {
        ("R1", "Resistor"), ("D1", "Led"), ("U1", "Component")}
    by_name = {n.name: n for n in low.nets}
    assert {(n.part_ref, n.pin) for n in by_name["LED_ANODE"].nodes} == {("R1", "P2"), ("D1", "A")}
    assert {(n.part_ref, n.pin) for n in by_name["VCC"].nodes} == {("R1", "P1"), ("U1", "VCC")}
    assert {(n.part_ref, n.pin) for n in by_name["GND"].nodes} == {("D1", "K"), ("U1", "GND")}
    assert {(n.part_ref, n.pin) for n in by_name["EN"].nodes} == {("U1", "EN")}
    # NotConnected pin dropped; config vars produced no spurious warnings
    assert not any(d.code == "Z002" for d in diags)


def test_import_led_indicator_on_the_current_surface():
    """The same board written against 0.4.41's surface must import identically —
    same parts, same net partition — and additionally carry what only the current
    surface can express: real pads, and a manufacturer beside the mpn."""
    legacy, _ = import_zen(ZEN_SRC, "led_indicator.zen")
    current, diags = import_zen(
        (FIXTURES / "led_indicator_v04.zen").read_text(), "led_indicator_v04.zen")
    assert [d for d in diags if d.severity == "error"] == []
    assert not any(d.code == "Z002" for d in diags)  # u1_part is not a net
    assert structure(current) == structure(legacy)
    u1 = next(n for net in current.nets for n in net.nodes
              if n.part_ref == "U1" and n.pin == "EN")
    assert u1.pad == "3"  # from pin_defs; the legacy fixture cannot say this
    attrs = dict(next(p for p in current.parts if p.ref == "U1").attrs)
    assert attrs["mpn"] == "TPS22918" and attrs["manufacturer"] == "Texas Instruments"


# ---------- gate: round-trip structurally ----------
def test_zen_round_trip_from_import():
    low1, _ = import_zen(ZEN_SRC, "led_indicator.zen")
    low2, diags = import_zen(emit_zen(low1, "led_indicator.zen"), "reexport.zen")
    assert [d for d in diags if d.severity == "error"] == []
    assert structure(low1) == structure(low2)


def test_zen_round_trip_from_ohm_designs():
    for design in sorted(GOLDEN.glob("*.ohm")):
        result = compile_source(design.read_text(), str(design))
        assert result.ok
        exported = emit_zen(result.low, design.name)
        reimported, diags = import_zen(exported, design.name + ".zen")
        assert [d for d in diags if d.severity == "error"] == []
        names = zen_refs(result.low)
        assert structure(result.low, names)[1] == structure(reimported)[1], design.name
        assert set(names.values()) == {p.ref for p in reimported.parts}


# ---------- the emitted surface, pinned to what the real tool accepts ----------
NCP_SRC = (
    'component NCP:\n'
    '    package = "TSOT-23-5"\n'
    '    mpn = "NCP1529ASNT1G"\n'
    '    manufacturer = "onsemi"\n'
    '    pin VIN: role=power_in, pad=1\n'
    '    pin GND: role=ground, pad=2\n'
    'module M:\n'
    '    a = NCP()\n'
    '    b = NCP()\n'
    '    a.VIN ~ b.VIN\n'
    '    a.GND ~ b.GND\n')


def test_hierarchical_refs_become_legal_zener_names():
    """`mcu.u` is a legal Ohm ref and an illegal Zener instance name: 0.4.41 says
    `Component name cannot contain invalid characters "."`. The export must not
    hand that file to the tool, and must not collapse two refs onto one name."""
    taken = {}
    assert _refname("mcu.u", taken) == "mcu_u"
    assert _refname("mcu_u", taken) == "mcu_u_2"  # collision broken, not merged
    assert _refname("mcu.u", taken) == "mcu_u"    # stable for the same ref
    result = compile_source((GOLDEN / "i2c_bus.ohm").read_text(), "i2c_bus.ohm")
    for line in emit_zen(result.low, "i2c_bus.ohm").splitlines():
        if line.startswith("    name="):
            assert "." not in line, line


def test_export_uses_the_current_component_surface():
    """The emitted vocabulary, pinned so a future edit has to argue with a test.

    `pin_defs` rather than `symbol`, because Ohm has no symbol object to give and
    0.4.41 accepts either; `part=Part(...)` in preference to the deprecated bare
    `mpn=` where both halves are known. See ohm/emit_zen.py's docstring for the
    reasoning, and tests/test_zen_pcb.py for the real binary agreeing."""
    result = compile_source(NCP_SRC, "m.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    text = emit_zen(result.low, "m.ohm")
    assert 'pin_defs={"GND": "2", "VIN": "1"}' in text  # real pads, not pin names
    assert 'part=Part(mpn="NCP1529ASNT1G", manufacturer="onsemi")' in text
    assert "symbol=" not in text  # nothing to point it at — see the module docstring
    assert "\n    mpn=" not in text  # legacy kwarg unused when Part is formable


def test_export_falls_back_to_the_legacy_kwarg_when_part_cannot_be_formed():
    """`Part` requires mpn AND manufacturer, both non-empty. Ohm carries `mpn` far
    more often than `manufacturer`, and a half-known part is exported half known:
    the `bom.underspecified` pcbc then reports is the same finding Ohm's own M001
    makes about the source design, so it is passed on rather than papered over with
    an invented manufacturer or a `skip_bom` that would silence a true complaint."""
    result = compile_source((GOLDEN / "supply.ohm").read_text(), "supply.ohm")
    text = emit_zen(result.low, "supply.ohm")
    assert 'mpn="NCP1529ASNT1G"' in text
    assert "Part(" not in text and "skip_bom" not in text and "manufacturer" not in text


# ---------- the importer reads both surfaces ----------
LEGACY_ZEN = (
    'V = Net("V")\n'
    'Component(name="U1", footprint="SOT-23-5", prefix="U", mpn="X",'
    ' pins={"VCC": V})\n')

CURRENT_ZEN = (
    'V = Net("V")\n'
    'p = Part(mpn="X", manufacturer="TI")\n'
    'sym = Symbol(library="U.kicad_sym")\n'
    'Component(name="U1", footprint="SOT-23-5", prefix="U", symbol=sym,\n'
    '          pin_defs={"VCC": "3"}, pins={"VCC": V}, part=p)\n')


def test_import_reads_both_the_legacy_and_the_current_component_forms():
    """A .zen importer faces files other people wrote — ADR-021's argument for
    structural KiCad readers, applied to Zener. The form Ohm emitted until
    2026-08-31 and the form it emits now must both come in, and `part=Part(...)`
    must be read whether written inline or bound to a variable first, which is what
    the published spec's own Component example does with its symbol."""
    for src in (LEGACY_ZEN, CURRENT_ZEN):
        low, diags = import_zen(src, "x.zen")
        assert [d for d in diags if d.severity == "error"] == [], src
        assert not [d for d in diags if d.code == "Z002"], src  # sym/p are not nets
        assert [(p.ref, p.package) for p in low.parts] == [("U1", "SOT-23-5")]
        assert dict(low.parts[0].attrs)["mpn"] == "X"


def test_only_the_current_form_can_say_which_pad_a_pin_lands_on():
    """`pin_defs` fills `ir.NetNode.pad`, which this importer used to leave None on
    every node — that None is why `ohm convert` had to invent pads from pin names."""
    assert import_zen(LEGACY_ZEN, "x.zen")[0].nets[0].nodes[0].pad is None
    low, _ = import_zen(CURRENT_ZEN, "x.zen")
    assert low.nets[0].nodes[0].pad == "3"
    assert dict(low.parts[0].attrs)["manufacturer"] == "TI"


def test_a_rejected_duplicate_cannot_overwrite_the_kept_parts_pads():
    """Z005 keeps the first instance of a repeated name — and that has to mean the
    whole of the first instance. `self.pads` is keyed by (ref, pin), so recording a
    duplicate's pin_defs would silently repin the part that was kept."""
    low, diags = import_zen(
        'V = Net("V")\n'
        'Component(name="U1", pin_defs={"A": "1"}, pins={"A": V})\n'
        'Component(name="U1", pin_defs={"A": "99"}, pins={"A": V})\n', "x.zen")
    assert [d.code for d in diags if d.severity == "error"] == ["Z005"]
    assert len(low.parts) == 1
    assert [n.pad for n in low.nets[0].nodes] == ["1"]


def test_inline_part_wins_over_the_deprecated_bare_mpn():
    """0.4.41 accepts both on one call but honours `part=`; a file carrying both is
    a file mid-migration, and Ohm records the field the tool would use."""
    low, _ = import_zen(
        'V = Net("V")\n'
        'Component(name="U1", mpn="OLD", part=Part(mpn="NEW", manufacturer="TI"),\n'
        '          pin_defs={"A": "1"}, pins={"A": V})\n', "x.zen")
    assert dict(low.parts[0].attrs)["mpn"] == "NEW"


def test_export_is_deterministic():
    result = compile_source((GOLDEN / "i2c_bus.ohm").read_text(), "i2c_bus.ohm")
    assert emit_zen(result.low, "x").encode() == emit_zen(result.low, "x").encode()


def test_zen_syntax_error_diagnostic():
    low, diags = import_zen("Component(name=\n", "bad.zen")
    assert diags[0].code == "Z001" and diags[0].severity == "error"
    assert low.parts == ()


def test_build_path_never_imports_zen_interop():
    """.zen stays interop-only (N5/ADR-002): ohm build must not touch it."""
    import ohm.driver as driver_mod
    import ohm.lower as lower_mod

    for mod in (driver_mod, lower_mod):
        src = Path(mod.__file__).read_text()
        assert "imp_zen" not in src and "emit_zen" not in src
