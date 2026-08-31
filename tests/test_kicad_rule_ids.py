"""Rule ids are version-sensitive keys, and Ohm keys behaviour off them.

Ohm supports KiCad 7, 8, 9 and 10 from one set of emitted bytes
(`ohm.cli.SUPPORTED_KICAD`), and in two places it decides what something MEANS
by looking at a KiCad rule id:

- `ohm/drc.py`'s `_INFO_RULES` decides which V007 geometry findings are advisory
  rather than defects;
- `ohm/emit_kicad_pro.py`'s `erc.rule_severities` decides which ERC rules KiCad
  must treat as errors.

KiCad renames those ids between releases — KiCad 10 renamed `label_dangling` to
`isolated_pin_label` — and a rename does not fail anything. It changes the
answer. A demoted finding comes back as a warning at the geometry gate; a
severity Ohm asked for is silently not applied. Both are the kind of defect that
is only ever found by someone wondering why two machines disagree.

That was recorded in CHANGELOG.md and PROGRESS.md and had no test. This file is
the test: it makes dropping a spelling FAIL rather than change behaviour quietly.

The table below is written out independently of the source it checks, which is
the whole point — a table derived from `_INFO_RULES` would agree with any edit
to `_INFO_RULES`, including the wrong one. Supporting a new KiCad means adding a
row here, and that is a visible edit in a diff.
"""

import json
from pathlib import Path

from ohm.drc import parse_drc_report
from ohm.driver import compile_source
from ohm.emit_kicad_pro import emit_kicad_pro

from tests.test_drc_gate import REPORT

EXAMPLE = Path(__file__).parent.parent / "examples" / "divider_led.ohm"

# (older spelling, newer spelling, the KiCad major that introduced the newer one)
#
# "Two spellings of one finding" is Ohm's reading, not a claim about KiCad's
# internals. For the footprint pair the recorded evidence is a violation count on
# one board, not a rename mapping (see the comment in ohm/drc.py); Ohm classifies
# both alike either way, and that is what is asserted here.
DRC_ALTERNATE_SPELLINGS = (
    ("lib_footprint_issues", "footprint_link_issues", "10"),
)
ERC_ALTERNATE_SPELLINGS = (
    ("label_dangling", "isolated_pin_label", "10"),
)


def _graded(text: str) -> dict[str, str]:
    """rule id -> the severity Ohm gave it, from a DRC report."""
    return {d.message.split(": ", 1)[0].split("x ", 1)[1]: d.severity
            for d in parse_drc_report(text, "b.kicad_pcb")}


def test_a_renamed_drc_rule_is_graded_exactly_as_its_older_spelling_was():
    """The behavioural half: the SAME report, respelled the way KiCad 10 spells
    it, must produce the same verdict it produced under the older spelling.

    This is what actually protects the gate. Asserting the contents of
    `_INFO_RULES` would only restate the source; this runs the classifier over
    both spellings of the same violations and compares the answers, so it fails
    whichever spelling is dropped.
    """
    renamed = REPORT
    for was, now, _kicad in DRC_ALTERNATE_SPELLINGS:
        assert f"[{was}]" in REPORT, (
            f"{was} is no longer in the sample report, so this test would pass "
            "without checking anything")
        renamed = renamed.replace(f"[{was}]", f"[{now}]")

    forward = {was: now for was, now, _ in DRC_ALTERNATE_SPELLINGS}
    before = _graded(REPORT)
    expected = {forward.get(rule, rule): sev for rule, sev in before.items()}
    assert _graded(renamed) == expected, (
        "a KiCad 10 report is graded differently from the KiCad 9 report it "
        "renames — a rule id fell out of ohm.drc._INFO_RULES")


def test_both_spellings_of_a_renamed_drc_rule_are_advisory():
    """And pin the verdict itself, so the test above cannot pass by grading both
    spellings wrongly in the same way. An unresolvable footprint library is not
    a geometry defect: Ohm embeds the geometry and ships no fp-lib-table."""
    renamed = REPORT
    for was, now, _kicad in DRC_ALTERNATE_SPELLINGS:
        renamed = renamed.replace(f"[{was}]", f"[{now}]")
    graded = _graded(REPORT) | _graded(renamed)
    for was, now, _kicad in DRC_ALTERNATE_SPELLINGS:
        assert graded[was] == "info", was
        assert graded[now] == "info", now
    # the control: a real geometry violation is still a defect under either run
    assert graded["courtyards_overlap"] == "error"


def test_the_project_names_a_renamed_erc_rule_the_way_every_kicad_spells_it():
    """The .kicad_pro cannot be version-conditional — Ohm emits one file for
    KiCad 7 through 10 — so a severity it wants enforced has to appear under
    every spelling those versions use. Only KiCad itself could prove the
    severity is applied; nothing in this repo runs ERC (KiCad 7 has no headless
    ERC at all). What this asserts is the part Ohm controls: that the key the
    running KiCad will look for is present, whichever KiCad it is.
    """
    result = compile_source(EXAMPLE.read_text(encoding="utf-8"), str(EXAMPLE))
    doc = json.loads(emit_kicad_pro(result.low, str(EXAMPLE), result.design))
    severities = doc["erc"]["rule_severities"]
    for was, now, kicad in ERC_ALTERNATE_SPELLINGS:
        assert was in severities, (
            f"{was} is gone from the emitted project; KiCad before {kicad} "
            "would fall back to its own default severity")
        assert now in severities, (
            f"{now} is missing from the emitted project; KiCad {kicad}+ does "
            f"not know {was} and would fall back to its own default severity")
        assert severities[was] == severities[now], (
            f"{was} and {now} are the same rule under two names and must carry "
            "the same severity, or the verdict depends on the KiCad installed")
