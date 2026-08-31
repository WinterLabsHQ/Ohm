# 08 · `ohm signoff --target-kicad` — the last check before fabrication

Loop 9 · 2026-08-31 · commit `5cd9415`

---

## What it was

`ohm signoff <src.ohm> <routed.kicad_pcb>` compiles the source, then verifies the
routed board against the resulting IR-LOW — checks V001 to V007. It is the last
gate before a board is made.

`ohm build` and `ohm check` accept `--target-kicad {7,8,9,10}`, fall back to
`ohm.toml`'s `target`, validate it, and thread it into `compile_source`. From
there `lower()` applies `_pad_translator`, which rewrites a declared pad id into
the **target KiCad's library vocabulary**.

**`signoff` did none of it.** No flag, no `ohm.toml` consultation, and
`compile_source(source, args.file, emit=True)` with no target.

## Why that mattered

KiCad 10 normalised shield and shell pads to `"SH"` across 163 footprints. So a
user who laid out on KiCad 10 has a board carrying `SH`, while signoff's source
model — built with no target, therefore untranslated — still says `11`.

`ohm/signoff.py:266-269` skips pads that are absent from the board. So the
mismatch was **not reported at all**. Not a false V001 — *silence*. A pin that is
genuinely unconnected on the fabricated board passed the last check before
fabrication, and the report said the board matched its source.

---

## What changed

`ohm/cli.py`: `signoff` gains `--target-kicad`, declared exactly as `build` and
`check` declare it — deliberately **without** argparse `choices`, so an unknown
value gets Ohm's own message (`unknown KiCad target '99' — supported: 7, 8, 9,
10`) rather than argparse's.

Precedence is **factored, not duplicated**: the existing
`if args.command in ("build", "check")` blocks became `("build", "check",
"signoff")`, so signoff walks the same `project.discover()` then `_target_major()`
then default then validate path. Resolution order is flag, then `ohm.toml`
`target`, then `"7"`.

Two signoff-specific deltas, both commented in place:

- `build` consults `ohm.toml` only when it is already relying on the project
  (file or `-o` omitted). `signoff` always names its file, so that rule would
  mean `ohm.toml` is *never* read. The discovery condition is widened for it.
- `signoff` discovers from `Path(args.file).parent`, not the cwd. The project
  that owns the design is the one that records which KiCad it targets, whatever
  directory the command was typed in.

**`ohm/signoff.py` needed no change, and that was verified rather than assumed.**
The path is `cli` to `compile_source(target_kicad=)` to `driver.lower(...)` to
`_pad_translator`, which rewrites `node.pad` on the IR-LOW net nodes; `signoff()`
reads `low.nets[].nodes[].pad`, so translated pads arrive on their own. A test
asserts it at the IR level: the pad set is `{"SH", "6"}` under target 10 and
`{"11", "6"}` under `None`.

---

## What proves it

`tests/test_signoff_target.py`, seven tests. The renamed case is **selected at
run time** from the shipped table rather than hardcoded — it walks candidates
keeping the first where the rename is real at 10 and the pad genuinely does not
translate at 7, and **raises rather than skips** if the table ever stops
containing such a case.

The falsifiable assertion is the *defective* board. Because a
`package = "Lib:Footprint"` part is emitted pad-less on purpose, a plain emitted
board has no pads to compare at all; the test hydrates the stub with the pads
KiCad would put there — the "laid out on KiCad 10" simulation — and leaves the
shield pad bound to no net, which is the exact fabrication defect the feature
exists for:

- `signoff --target-kicad 10` gives exit 1 and
  `V001 … j1.SH … bound to net '' … source says 'J1_SHIELD'`
- the same board with no flag gives **exit 0 and no V001** — silence, as
  described.

**Verified by mutation:** removing `target_kicad=args.target_kicad` from the
signoff branch fails two tests; removing the parser flag makes argparse exit 2 on
the same tests.

The other five cover the ordinary CLI surface: a good KiCad 10 board still signs
off clean with the target (so the flag cannot manufacture a V001), every value in
`SUPPORTED_KICAD` is accepted, an unknown value is refused with `build`'s exact
message *before* the source is read, `ohm.toml` alone catches the defective
board, and an explicit flag overrides `ohm.toml`.

---

## Found, not fixed

`ohm check` **has** the flag, but like `build` it consults `ohm.toml`'s `target`
only when it is already relying on the project — so `ohm check src.ohm` inside a
`target = "kicad10"` project silently uses 7. Reported and deliberately not
changed: it is a different command's precedence rule, and changing it belongs
with a decision about that rule rather than being folded into this fix.
