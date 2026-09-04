# 09 · Read-back hardening — the direction that has broken three times

Loop 10 · 2026-08-31 · commit `0295cd9`

---

## What it was

Ohm **writes** the KiCad 7 format and relies on KiCad reading forward, which is
safe because KiCad guarantees that direction. Ohm's **readers** face the other
way — `ohm/signoff.py`, `ohm/import_symbol.py` and `ohm/provenance.py` parse
files the *user's* KiCad wrote, which is whatever version they installed.

That direction has already broken three times, each found by a human running a
new KiCad rather than by a test:

- KiCad 9 pretty-printed `(class "Default")` differently;
- KiCad 10 split nets across lines;
- KiCad 10 dropped the top-level net table, which made `ohm signoff` report V001
  on **every pad of every board it had saved**.

After those, the readers were made **structural** — they walk the s-expression
tree rather than matching lines. They were **not** made **defensive**. An unmet
shape returned a silently wrong answer rather than saying which shape was
missing, and several raised straight out of the last gate before fabrication.

**The two verdicts are opposites.** "Your board does not match its source" and
"I could not read your board" look identical from outside and mean entirely
different things. Only one of them is about the board.

---

## What changed — V008

A new signoff code, **error** severity, emitted *before* every finding computed
from the board, with help that says plainly:

> signoff walks the board's S-expression tree structurally, so this is a shape it
> expected and did not find — likely a KiCad version writing something new. Treat
> every other finding below as unreliable, and report the KiCad version that
> wrote this file.

Eight shapes, each naming what was not found and what it means:

| Shape | What it used to do |
|---|---|
| No `(kicad_pcb …)` form at all | Empty board → "part 'r1' is missing" for **every** part |
| N footprints, **none** carrying `ohm_path` | V001 on everything — signoff identifies parts by that property |
| Footprint `(at)` with no coordinates | `IndexError` |
| Pad `(at)` / `(size)` with no numbers | `IndexError`; a dropped pad reads as V002 "incomplete net" |
| Track/via coordinates unreadable | `IndexError`; invisible copper makes routed nets look unrouted |
| No pad names a copper layer | V002 + V003 — no track can reach any pad, so every net looks unrouted |
| Stray `)` | `IndexError` from popping an empty stack |
| Unclosed `(` | Silently dropped everything read — a truncated file read as **an empty layout** |

### Where tolerance was kept, deliberately

Robustness must not become chatter, so a miss that is locally recoverable stays
silent:

- **An absent rotation** — the normal case for most parts.
- **A keyword in the rotation slot.** `(at x y 90 unlocked)` is KiCad's own shape
  and used to raise `InvalidOperation`.
- **An unnamed mechanical pad.** `(pad smd circle …)` previously produced a pad
  *called* `"smd"`, which matches no source pad and reads as drift. Now decided by
  shape, not version.
- **A non-copper pad**, which is legal.
- **One unidentified footprint.** A board legitimately carries parts Ohm never
  placed — mounting holes, logos, user additions — and one of those *is* a real
  V001. The `ohm_path` check is therefore **board-level**: N footprints and none
  carrying it.

### The worst instance was not in signoff

`ohm/import_symbol.py` assumed pins live only in unit sub-symbols. A library
declaring pins **directly on the symbol** came back with zero pins, and the user
was told:

> `'Direct' has no pins (graphic-only symbol?)`

A confident claim about the user's part, produced by a failure to read the file.
Pin discovery is now recursive. Separately, a nameless symbol form used to be
offered as a *suggestion*, stringified parse tree and all:

> `symbol 'R' not in library (near: ['property', 'Reference', 'R', ['at','0','0','0']])`

`ohm/provenance.py` had the same shape one level up: a library that failed to
parse and a library that lacks the symbol were both an empty dict, so the report
said "not found in `<dir>`" — a statement about the *ecosystem* when the truth was
that Ohm could not read the file, sending the author hunting for a symbol sitting
right there.

---

## What proves it

**26 new tests, all hermetic.** Hand-written fixtures are the point: the whole
risk is a KiCad that does not exist yet, so waiting for one is not a test
strategy.

Non-vacuity was established by running the **pre-change** readers against every
fixture. Six raised `IndexError` or `InvalidOperation`; four more returned a
confident wrong verdict:

| Broken shape | Before | Now |
|---|---|---|
| renamed `ohm_path` | V001 only | V008 + V001 |
| footprint `(at)` with no coords | **IndexError** | V008 |
| pad `(size)` with no numbers | **IndexError** | V008 |
| stray `)))` | **IndexError** | V008, board still read |
| `(at x y unlocked)` | **InvalidOperation** | clean, no V008 (deliberate) |
| truncated file | V001 only | V008 |
| pads on no copper layer | V002 + V003 | V008 |
| pins declared on the symbol | "graphic-only symbol?" | imports 2 pins |

Plus a **control**: an Ohm-emitted board, routed and unrouted, produces **zero**
V008.

Certified at `kicad/kicad:7.0.11` and `10.0.5` — 0 failed, **0 skipped** each.
(The pass count moves as tests are added;
[21_compatibility_matrix.md](21_compatibility_matrix.md) carries the current one
and is the single place to look it up.)

### A gate that had never looked at this tier

`tests/test_p10.py::test_every_emitted_code_has_an_explanation` scanned for
`[PRUSICQZKBM]\d{3}`. **`V` was not in that class**, so V001 through V007 were
never covered: the check that every code carries a cause and a fix skipped the
entire tier that runs last, on a routed board, immediately before fabrication.
`V` and `D` added — **111 codes covered instead of 99** — and the test now also
asserts it found something, because a regex matching nothing would have passed
forever. That is precisely how `V` went unnoticed.

**And 111 was one short.** `X000` (`ohm/lsp.py`) was outside the class too, and
the docstring written here claimed the class was now derived from `EXPLAIN`
rather than hand-kept — which it was not. Fixing a hand-kept allowlist by hand
leaves a hand-kept allowlist. The class is now the whole alphabet
(`[A-Z]\d{3}`, measured: no false positives anywhere in `ohm/`), the check runs
in **both** directions so an explanation for a deleted code is caught too, and
the anti-vacuity floor is tied to `len(EXPLAIN)` instead of a literal. See
[25_second_sweep.md](25_second_sweep.md).

---

## Still not proven

- **`ohm/check_drawing.py` is line-anchored** — `sch.find("\n  (lib_symbols")`,
  `body.split("\n  (symbol (lib_id ")`. Exactly the pattern ADR-021 forbids. It
  reads Ohm's *own* emitted schematic, so today it is exposed to Ohm's formatting
  rather than to KiCad's — but a two-space indentation change in `emit_kicad.py`
  would make the D-tier checks go silently empty. Same failure class, different
  blast radius. Found, not fixed.
- **`ohm/verify_footprints.py` is a fourth reader** of user-installed KiCad files
  and is regex-based. It already abstains on an empty pad set, which is the right
  instinct; the residual exposure is a *partial* miss — a future format that let
  the regex read some pads but not others would report "pad X is not in this
  footprint", a mismatch verdict from a parse gap.
- V008 covers the shapes that exist today. The next KiCad can invent one nobody
  anticipated; what the code now guarantees is that it will **say so** rather than
  blame the board.
