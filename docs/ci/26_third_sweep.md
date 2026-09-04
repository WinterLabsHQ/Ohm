# 26 — the third sweep: the drawing gate's own corpus, and the shape of the code

Started 2026-09-03 · branch `ci1-eda-upgrade` · baseline commit `2e9f203` plus
the working tree of round 25

Round 25 asked what the gates could not see. This one asks two narrower
questions, and both were answered by measurement rather than by reading:

1. **The gates that exist — do they measure what they claim?** Two of the D
   tier's three geometric inputs were being read wrong, so the tier was reporting
   overlaps at coordinates the drawing does not have.
2. **Does the code still have the shape the documents describe?** The spine does.
   The module graph had grown three mutual dependencies, each held apart only by
   an import written inside a function body.

The core architecture is unchanged. No pass was added to, removed from or
reordered in `driver.CHECKERS`; no IR shape moved; no emitted format version
moved. One module was added — `ohm/emit_common.py` — and it holds nothing new,
only what two emitters were already reaching into each other for.

---

## The defects, reproduced

### 1. A diagnostic quoted a file it was not about

`ohm build examples/divider_led.ohm --emit kicad` printed:

```
out/divider_led.kicad_sch:1:1: warning[D002]: 1 piece(s) of text print over a symbol
    # Example: 5V rail feeding a sense divider + an indicator series resistor,
    ^
```

The location names the emitted schematic. The line quoted beneath it, with a
caret under it, is line 1 of `divider_led.ohm` — a different file, whose line 1
has nothing to do with the finding.

`_report` renders one source text against every diagnostic in the list. That was
correct while the list held only diagnostics about the `.ohm`; when round 25
brought the D tier into the same list — so that `--strict` and
`--json-diagnostics` could see it, which was right — every D finding started
being quoted against the wrong file.

**Fixed** by giving `_report` the path that `source` is the text *of*, and
rendering an excerpt only for diagnostics about that file. `human(None)` still
prints the location, the message and the help line, so nothing is lost for the
others. Gated by
`test_a_drawing_diagnostic_is_not_quoted_against_the_ohm_source`.

### 2. The drawing gate could not see a `(justify …)` on a property

```python
_PROP_RE = re.compile(
    r'\(property\s+"[^"]*"\s+"((?:[^"\\]|\\.)*)"\s*\(at\s+…'
    r'\(effects\s*\(font\s*\(size\s+([\d.]+)[^)]*\)\)([^)]*)\)', re.S)
                                                     ^^^^^^^
```

The trailing group stops at the first `)`. `(justify left)` **contains** a `)`,
so the group could never hold one: `_JUST_RE.search(m.group(5))` returned `None`
for every property Ohm writes with a justification, and every one of them was
measured as centred. Measured on `examples/io_expander_board.ohm`: a `1uF` value
drawn from x=111.76 rightward was boxed at 110.236–113.284, which is 1.52 mm left
of where it is drawn.

`hide` — the other thing that group is read for — is a bare word, which is why
that half went on working and hid the other half for as long as it did.

**Fixed** with a group that admits one level of nesting,
`((?:[^()]|\([^()]*\))*)`. The same pass tightened the label branch, which reads
its justification from a fixed 120-character window after the match: on a short
label that window reaches past the element's closing paren into the *next*
label's `(justify …)`. It now stops at the element's own `(uuid`.

### 3. `check_drawing._symbols()` was dead, and wrong

Fifteen lines computing symbol envelopes, called by nothing. `check_drawing()`
does the same computation inline, because it also needs each instance's own text.

It was not merely unused. `_body_half` finds a symbol's geometry in the
`lib_symbols` block, and `_symbols`'s docstring implies it is called on the sheet
*body* — which is the text with `lib_symbols` removed. Called that way every
symbol comes back on the 7.62 mm fallback: an `ohm:Capacitor` measures
15.24 × 15.24 mm instead of 1.52 × 4.06, and two parts a comfortable 10 mm apart
overlap by 5 mm. **Deleted**; the live inline computation is the only one.

### 4. Four shipped designs failed Ohm's own drawing gate

With the measurement corrected, the corpus still had four findings — all real,
all the same shape: **text Ohm placed itself, in a column something else had
already claimed.**

| Design | | Measured |
|---|---|---|
| `examples/divider_led.ohm` | D002 | `PWR_GND`, 7.11 mm of text, on a 2.54 mm wire; the capacitor beside it starts 4.32 mm along |
| `examples/io_expander_board.ohm` | D001 | a turned rider's `1uF` at 111.76–114.81 against a pin label `VIN_V` at 111.76–116.84 |
| `tests/examples/led_panel.ohm` | D001 | a cluster title `Decoupling` drawn across a ladder head's `PWR_V` |
| `tests/examples/sensor_node_bme280.ohm` | D001 | the same turned-rider pair as io_expander |

Three distinct causes, and each was fixed at the place that chose the position:

- **`_rider_offset` reserved nothing for the wire's label.** `_chain_span`
  already reserves `_rail_name_width` for an *upright* rider — "the rail it
  terminates on still prints its name HORIZONTALLY, and that name is as wide as
  any label" — and the in-line case, which is most of them, reserved a flat
  2.54 mm. It now reserves the label's own width, read from the same `drawable`
  map that decides whether a label is emitted at all, so the space asked for and
  the text written cannot disagree.
- **A ladder head's rail name never reached `taken_text`.** It was the only
  placed text on the sheet that did not, so the cluster titles — which *are* free
  to move and *do* consult that list — were placed straight through it.
- **A symbol's reference and value never consulted it either.** They are free
  text; Ohm chooses where they go. They now go through `_free_spot` exactly as
  the frame titles do, with the position they have always had offered first, so
  nothing moves unless it must, and the part's own body excluded from the
  obstacles.

The corpus is now at **zero**, gated by
`test_every_shipped_design_draws_clean` over all 29 designs.

### 5. `_text_box` measured unjustified text half a string away from `_text_box`

The emitter and the gate each have one. For text with no `(justify …)` — which
is how KiCad draws a *centred* string, and how Ohm writes every untured
reference and value — `check_drawing`'s returns a centred box and
`emit_kicad`'s returned a left-aligned one.

Nothing passed `""` while only labels and titles came through the emitter's copy,
so it sat as a trap rather than a live defect. Defect 4's fix routes symbol
properties through it, which would have made it live. **Fixed** in the direction
that agrees with KiCad and with the gate.

### 6. S013 asserted a cause it had not established

On `near(ra, rb) near(rb, rc) near(rc, ra)`:

> `near(ra, rb)` asked for 2mm and the placement achieved 6.490mm: **every side
> of 'rb' was already taken**, so 'ra' was flowed into the next free cell instead

Traced: a cycle has no root, so the first seating pass places nobody, and the
loop breaks one edge by flowing `ra` — **before anything at all is on the
board**. `rb` had not been placed. The stated cause is not vague, it is false,
and the two causes have different fixes: more room does nothing for a cycle.

**Fixed** by distinguishing them, with the help text to match. Round 25's own
note that "a cycle *reads* as unsatisfiable and is not" is why this is measured
rather than rejected: a two-cycle is satisfied and stays silent.

### 7. Three documents asserted a registry size the registry had not had for a round

`ohm/formats.py`'s module docstring ("Of the fifteen entries below"), ADR-022
("of the fifteen registry entries") and `12_multi_eda_version_policy.md` all said
fifteen. It became nineteen when `.kicad_pro`'s three section versions and STEP's
DATA-section protocol name were registered — in round 25, by the same commit that
quoted the old number.

A number in prose is precisely the unnamed literal `ohm/formats.py` exists to
abolish, and the module had one of its own. **Fixed** in all three, and pinned:
`test_the_registry_is_the_size_its_documentation_says` fails on a size change and
names the three documents in its message. ADR-022 gains an amendment recording
the four late entries rather than being edited to look as though it surveyed
them.

---

## The shape of the code

`AGENTS.md`, the blueprint and every index here describe the same spine and the
same three invariants. Two invariants had gates — the hermeticity walk in
`test_lower_emit.py`, the byte comparison in `test_determinism.py` and
`tools/emit_manifest.py`. The **shape** had none. It was prose, and prose does
not fail.

Measured: three pairs of modules had become mutually dependent, every one held
apart only by an import written inside a function body rather than at the top of
a file.

| Cycle | What each took from the other |
|---|---|
| `check_placement` ↔ `emit_kicad` | the checker imports `placement`, `near_follows`, `_courtyard`; the emitter imported `_extent` back |
| `emit_kicad` ↔ `emit_netlist` | `_source_id` one way, `_value_text` and `BOM_FIELDS` the other |
| `import_symbol` ↔ `provenance` | `digest_pairs` one way, the KiCad symbol reader the other |

**The first two are fixed, and neither was the shape of a real relationship.**

`_extent` is the axis-aligned half-extent of a rotated rectangle — pure geometry,
needed by the placer and by the check. It lived in the checker, so the emitter
had to reach into a checker to ask what a rectangle is. It now sits with
`_courtyard` and the rest of the placement geometry, and the dependency runs one
way: the checker asks the emitter what the placement is, which is the point of
S011 and S013. That import is now declared at module level, where a reader can
see it, because it no longer has to hide.

`_source_id` was written for schematic uuids and then needed by every artifact
that carries a file name, so five emitters imported it from `emit_kicad` — a
module that was never what it was about. It, `_value_text` and `BOM_FIELDS` are
now **`ohm/emit_common.py`**, below every emitter and depending on none. That
removes five `emit_netlist → emit_kicad`-shaped edges as well as the cycle.

**The third is declared and allowed.** `provenance` owns what a pinout digest
*is*; `import_symbol` owns how a KiCad library is *read*. Each needs a piece of
the other, both directions are documented at their call sites, and splitting them
would mean a third module holding one hash function.

`tests/test_architecture.py` is the new gate: no import cycle outside a declared
allowlist, the checker list is the one the documents name, `shared/` does not
import from `ohm/`, and the non-mutation invariant is *run* over the corpus
rather than asserted.

---

## What this round did NOT prove

- **The four drawing fixes are measured by Ohm's own gate, not by KiCad.** The
  corpus is at zero D findings and no wire endpoint lands mid-wire without a
  junction (checked separately), but `tests/test_schematic_roundtrip.py` — the
  oracle that makes KiCad re-read the sheet — needs a KiCad, and there is none on
  this machine. The four `kicad` matrix legs run it; it has not run since these
  changes.
- **`_free_spot` still falls back to its first candidate** when every position it
  is offered collides, rather than to the least-intrusive one. On the corpus this
  is unobservable: measured across all fourteen candidates for the one case that
  used to fail, the first candidate *was* the minimum. It is recorded because the
  next crowded sheet may not be so lucky.
- **Nothing here touched the emitted format versions, the pass order, or the IR.**
  That is the intent, and `test_architecture.py` and `test_formats.py` now say so
  rather than leaving it to a reader's inspection.
