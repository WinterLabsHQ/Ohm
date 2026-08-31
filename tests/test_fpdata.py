"""S012: the pads an external footprint actually carries.

The gap this closes: `package = "Lib:Footprint"` emits a pad-LESS stub, so the
pad id is the only thing that reaches KiCad. S003 proves the pin->pad map is
total; nothing proved the pads it names exist. KiCad 10 renamed shield and shell
pads to "SH" across 163 footprints, which leaves `pin SHIELD: pad=11` silently
unconnected on every board that upgraded.

Everything here reads the table shipped inside `ohm.fpdata` — no KiCad, no
network, no disk outside the package — so it runs everywhere.
"""

from ohm import fpdata
from ohm.driver import compile_source

MICROSD = "Connector_Card:microSD_HC_Hirose_DM3AT-SF-PEJM5"
# removed from KiCad's own libraries after 8 — a different, more serious defect
# than a renamed pad, and the table records it as absent rather than unknown
REMOVED = "Button_Switch_SMD:SW_SPST_EVQP2"
# the opposite direction: KiCad ADDED this after 7, so on a KiCad 7 target it is
# missing for a reason that needs the opposite fix from a removal. 728 footprints
# are in this class; Ohm's own stdlib named one of them until 2026-08-31.
ADDED_AFTER_7 = ("TerminalBlock_CUI:"
                 "TerminalBlock_CUI_TB007-508-02_1x02_P5.08mm_Horizontal")

SRC = """component MicroSD:
    package = "{fp}"
    designator_prefix = "J"
    pin DAT2: role=passive, pad=1
    pin CD: role=passive, pad=2
    pin CMD: role=passive, pad=3
    pin VDD: role=power_in, pad=4
    pin CLK: role=passive, pad=5
    pin VSS: role=ground, pad=6
    pin DAT0: role=passive, pad=7
    pin DAT1: role=passive, pad=8
    pin CDET: role=passive, pad=9
    pin GNDPAD: role=ground, pad=10
    pin SHIELD: role=ground, pad=11

module Board:
    j1 = MicroSD()
"""

# The translation tests need the shield on an actual net: an unconnected pin
# never reaches the netlist, so there would be no emitted pad to inspect.
WIRED = SRC + "    j1.SHIELD ~ j1.VSS\n"

GENERATED = """component Part:
    package = generate(SOIC, pins=2)
    designator_prefix = "U"
    pin A: role=passive, pad=1
    pin B: role=passive, pad=11

module Board:
    u1 = Part()
"""


def _s012(source: str, target: str | None) -> list:
    result = compile_source(source, "sd.ohm", emit=False, target_kicad=target)
    return [d for d in result.diagnostics if d.code == "S012"]


def test_the_shipped_table_loads_and_is_self_consistent():
    majors = fpdata.kicad_majors()
    assert majors and all(m.isdigit() for m in majors)
    assert fpdata.known(MICROSD)
    for major in majors:
        assert fpdata.pads(MICROSD, major)


def test_the_loader_abstains_on_an_unknown_footprint():
    assert not fpdata.known("MyLib:Thing")
    assert fpdata.pads("MyLib:Thing", "10") is None
    assert fpdata.majors_lacking("MyLib:Thing", "1") == ()


def test_the_loader_abstains_on_an_unknown_major():
    """A major the table has no dump for is UNKNOWN, never clean.

    This test used KiCad 7 as its unknown, on the premise that 7 could not be
    dumped because no container image existed below 8.0.4. That premise was
    false — `kicad/kicad:7.0.11` is real — and the table now carries 7 data, so
    the case moved to KiCad 11. The rule never depended on the example: a major
    with no dump is unanswerable whatever the reason it has no dump.
    """
    assert "11" not in fpdata.kicad_majors()
    assert fpdata.pads(MICROSD, "11") is None
    assert not fpdata.present_in(MICROSD, "11")
    assert fpdata.absence(MICROSD, "11") is None      # unknown, not "removed"


def test_every_supported_kicad_is_covered_by_the_table():
    """The table must answer for every version `--target-kicad` accepts.

    While these two sets disagreed, a target Ohm advertised as supported was one
    S012 silently abstained on — which is how Ohm's own standard library came to
    name a connector footprint that KiCad 7 does not have, on the DEFAULT target,
    with every gate green.
    """
    from ohm.cli import SUPPORTED_KICAD
    assert set(fpdata.kicad_majors()) == set(SUPPORTED_KICAD)


def test_absence_tells_added_later_from_removed():
    """The two reasons a footprint can be missing need opposite fixes.

    REMOVED means the part is gone and needs replacing. ADDED_LATER means the
    part is fine and this KiCad is older than it. S012 called both "no longer
    ships" until KiCad 7 data existed to tell them apart.
    """
    assert fpdata.absence(ADDED_AFTER_7, "7") == "added_later"
    assert fpdata.absence(ADDED_AFTER_7, "10") is None       # present
    assert fpdata.absence(REMOVED, "10") == "removed"
    assert fpdata.absence(REMOVED, "8") is None              # present
    assert fpdata.absence("MyLib:Thing", "10") is None       # unknown, not a verdict


def test_the_loader_abstains_on_a_footprint_that_version_removed():
    assert fpdata.known(REMOVED)
    assert fpdata.present_in(REMOVED, "8")
    assert not fpdata.present_in(REMOVED, "10")
    assert fpdata.pads(REMOVED, "10") is None


def test_a_real_rename_resolves():
    assert fpdata.renamed_to(MICROSD, "11", "10") == "SH"
    assert fpdata.renamed_to(MICROSD, "11", "9") is None
    assert "SH" in (fpdata.pads(MICROSD, "10") or set())
    assert fpdata.majors_lacking(MICROSD, "11") == ("10",)


def test_a_renamed_pad_is_translated_not_reported():
    """The pad KiCad renamed is the SAME copper, so Ohm writes the target's
    spelling into the netlist and says nothing. Reporting it would ask the author
    to maintain one source per KiCad, which is the problem, not the fix."""
    from ohm.lower import lower

    for target, wanted, gone in (("8", "11", "SH"), ("9", "11", "SH"), ("10", "SH", "11")):
        result = compile_source(WIRED.format(fp=MICROSD), "sd.ohm", emit=False,
                                target_kicad=target)
        low, _ = lower(result.design, "sd.ohm", target)
        emitted = {n.pad for net in low.nets for n in net.nodes}
        assert wanted in emitted, f"KiCad {target} should bind the shield to {wanted}: {emitted}"
        assert gone not in emitted, f"KiCad {target} still emitted {gone}: {emitted}"
        assert _s012(SRC.format(fp=MICROSD), target) == []


def test_translation_only_ever_renames_into_a_pad_the_target_has():
    """A guard on the translator itself: whatever it writes must exist in the
    target's footprint, or it has moved the connection to nothing."""
    from ohm.lower import lower

    for target in fpdata.kicad_majors():
        have = fpdata.pads(MICROSD, target)
        result = compile_source(WIRED.format(fp=MICROSD), "sd.ohm", emit=False,
                                target_kicad=target)
        low, _ = lower(result.design, "sd.ohm", target)
        for net in low.nets:
            for node in net.nodes:
                if node.pad:
                    assert node.pad in have, (
                        f"KiCad {target}: emitted pad {node.pad!r} that "
                        f"'{MICROSD}' does not have")


def test_no_target_is_silent_when_every_difference_is_a_rename():
    """Without --target-kicad, a pad Ohm can translate for every major is not a
    finding: the design is correct on all of them."""
    assert _s012(SRC.format(fp=MICROSD), None) == []
    assert _s012(SRC.format(fp=MICROSD), "7") == []


UNRESOLVABLE = SRC.replace("pin SHIELD: role=ground, pad=11",
                           "pin SHIELD: role=ground, pad=99")


def test_a_pad_that_no_rename_explains_is_still_reported():
    """The check must keep its teeth: pad 99 exists in no version and was never
    renamed into anything, so translation cannot save it."""
    diags = _s012(UNRESOLVABLE.format(fp=MICROSD), "10")
    assert len(diags) == 1 and diags[0].severity == "error"
    assert "'99'" in diags[0].message and MICROSD in diags[0].message
    assert "SH" in (diags[0].help or "")  # summarise() shows what it does have


def test_an_unresolvable_pad_warns_when_no_target_is_named():
    diags = _s012(UNRESOLVABLE.format(fp=MICROSD), None)
    assert len(diags) == 1 and diags[0].severity == "warning"


def test_s012_is_silent_on_the_versions_that_still_call_it_11():
    assert _s012(SRC.format(fp=MICROSD), "8") == []
    assert _s012(SRC.format(fp=MICROSD), "9") == []


def test_s012_reports_a_footprint_the_target_removed():
    src = 'component Sw:\n    package = "{fp}"\n    designator_prefix = "SW"\n' \
          "    pin A: role=passive, pad=1\n    pin B: role=passive, pad=2\n\n" \
          "module Board:\n    s1 = Sw()\n"
    diags = _s012(src.format(fp=REMOVED), "10")
    assert len(diags) == 1 and diags[0].severity == "error"
    assert "no longer ships" in diags[0].message and REMOVED in diags[0].message
    assert _s012(src.format(fp=REMOVED), "8") == []


def test_s012_abstains_on_an_unknown_library():
    assert _s012(SRC.format(fp="MyLib:Thing"), "10") == []
    assert _s012(SRC.format(fp="MyLib:Thing"), None) == []


def test_s012_abstains_on_a_generated_package():
    # pad 11 on a 2-pin SOIC is S002's business; S012 has no footprint to consult
    assert _s012(GENERATED, "10") == []
    assert _s012(GENERATED, None) == []


def test_the_check_command_accepts_a_target(tmp_path, capsys):
    from ohm.cli import main

    src = tmp_path / "sd.ohm"
    src.write_text(SRC.format(fp=MICROSD), encoding="utf-8")
    # the renamed shield is translated, so a KiCad 10 build of this is clean
    assert main(["check", str(src), "--target-kicad", "10"]) == 0

    src.write_text(UNRESOLVABLE.format(fp=MICROSD), encoding="utf-8")
    assert main(["check", str(src), "--target-kicad", "10"]) == 1
    assert "S012" in capsys.readouterr().err
