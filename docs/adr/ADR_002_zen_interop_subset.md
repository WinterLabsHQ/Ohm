# ADR-002 — .zen interop: bounded Starlark subset via Python `ast` (P7)

Date: 2026-07-23 · Status: accepted · Revised 2026-08-31 (section 5 closed — see
the revision at the end, which corrects decision 2)

## Context
`.zen` (Zener, diodeinc/pcb) files are Starlark modules — and Starlark syntax is a
subset of Python syntax by design. The blueprint mandates `.zen` as import/export
interop only (D1/N5), never a build dependency.

## Decision
1. **Parse `.zen` with Python's stdlib `ast`** — no Starlark interpreter dependency;
   the interop path stays pure-Python and hermetic.
2. **Bounded, documented subset** (matching the published Zener spec) — *the
   export half of this was wrong and is superseded; see the 2026-08-31 revision*:
   - `Component(name=, footprint=, prefix=, pins={...})`
   - `X = Module("path")` generics + `X(name=, value=, package=, <PIN>=net, ...)`
   - `x = Net("N")` / `Power("VCC", ...)` / `Ground()` / `NotConnected()` / `x.NET`
   - `io(...)` vars treated as boundary nets; `load(...)`, `interface(...)`,
     `config(...)` are accepted and skipped (no electrical content for flat interop)
3. **Interop happens at the flat level**: `.zen` ⇄ IR-LOW (parts + nets).
   Round-trip equivalence = structural: same parts (ref, footprint) and the same
   net partition of (part, pin) endpoints.
4. Not imported (documented loss): `Power(voltage=...)` annotations, `interface`
   definitions, config expressions. Import diagnostics use Z-codes.
5. The optional `pcb` toolchain cross-check (manual P7 gate) is **deferred** until
   the `pcb` binary is available locally; it lives on the interop path only and is
   never wired into `ohm build`. *(Closed 2026-08-31 — see the revision below.)*

## Consequences
Zero new dependencies; the subset is honest about what survives the trip; a full
Starlark evaluator can replace the `ast` walker later without touching the IR side.

## Revision — 2026-08-31: section 5 closed, and what it found

The `pcb` binary was obtained (diodeinc/pcb **0.4.41**, released 2026-08-31,
prebuilt for linux x86_64 gnu/musl, macOS aarch64 and Windows x86_64, with a
`stdlib.tar.zst` alongside). Decision 5's cross-check is no longer deferred:
`tests/test_zen_pcb.py` builds Ohm's emitted `.zen` with the real compiler and
skips — with the tool's own words in the skip reason — where the binary is absent
or cannot run. It is still on the interop path only and is still never wired into
`ohm build`; `tests/test_zen.py` continues to assert that `driver.py` and
`lower.py` mention neither `imp_zen` nor `emit_zen`.

**The export did not load.** Decision 2 described what Ohm wrote as the universal
`Component()` form. `pcbc build` rejects it twice:

```
Either `pin_defs` or a Symbol value for `symbol` must be provided
[bom.unspecified] Component 'U1' is included in the BOM but is missing part
information. Specify `part=Part(...)`.
```

A year of round-trip tests had proved only that Ohm's importer agreed with Ohm's
exporter. That is the cost of a deferral nobody revisits, and it is the reason
this revision is written as a correction rather than folded into decision 2.

### What changed

1. **`emit_zen` writes the current surface.**
   - `pin_defs={"<pin>": "<pad>"}` instead of nothing. Zener wants a pin→pad map
     before it accepts a Component, and normally that arrives with a
     `Symbol(library=...)`. **Ohm has no symbol object to give** — `emit_zen`
     receives IR-LOW, which carries parts, nets, pins and pads and no schematic
     geometry, and pointing `symbol=` at a `.kicad_sym` this export does not write
     would be a reference that resolves nowhere. `pin_defs` is the symbol-free half
     of the tool's own error message, and `ir.NetNode.pad` is exactly the data it
     asks for. Where a pin has no mapped pad the pin's own name is used, which is
     the fallback `emit_netlist.py` already applies.
   - `part=Part(mpn=..., manufacturer=...)` in preference to the bare `mpn=` /
     `manufacturer=` kwargs, which 0.4.41 accepts but reports as deprecated.
     `Part` requires **both** fields non-empty; Ohm carries `mpn` far more often
     than `manufacturer`, so where only one is known the legacy kwarg is written
     for that one and the file keeps a real `bom.underspecified` error. That is
     deliberate — it is the same gap Ohm's own M001 reports about the same design,
     and inventing a manufacturer or setting `skip_bom` would silence a true
     finding.
   - Instance names are sanitised. Ohm refs are hierarchical paths (`mcu.u`) and
     0.4.41 refuses them: `Component name cannot contain invalid characters "."`.
     Dots become underscores, collisions are broken stably, so the round trip is
     an equality under a bijective renaming rather than a literal one.
   - Keyword spacing follows `pcb fmt`, not the published spec: the spec's
     examples write `name = "U1"`, the formatter rewrites that to `name="U1"`.
     Emitted files are `pcb fmt --check` clean.
   - The header comment names the toolchain lane the file targets
     (`pcb-version = "0.4"`), which `ohm/formats.py` now pins — see ADR-022.

2. **`imp_zen` reads both surfaces.** The legacy form (which is what Ohm itself
   emitted until today, and what any older `.zen` looks like) and the current one,
   side by side. This is ADR-021's "readers must be structural, not
   version-anchored" applied to Zener: an importer that recognises one spelling
   breaks on the next release. New in the accepted vocabulary: `pin_defs` (which
   fills `ir.NetNode.pad`, previously `None` on every imported node),
   `part=Part(...)` both inline and via a variable, and the `manufacturer` and
   `type` kwargs. `symbol=` is accepted and deliberately **not** recorded — the
   pin→pad map it points at lives in a `.kicad_sym` this importer does not open,
   and an attr made from the library path would put a filename where a pinout
   belongs.

3. **`emit_ohm` uses real pads when the `.zen` stated them.** Its docstring used to
   open "zen carries no pad numbers", which was true of the subset the importer
   read and is not true any more. Stated pads are used verbatim; a pad guessable
   from the pin's name is still taken from the name; only where neither exists does
   a sequential placeholder and its loud TODO appear. A stated map is dropped for a
   whole component declaration if two instances sharing it disagree, or if it maps
   two pins to one pad — a visible TODO beats one instance's pinout shipped under
   another instance's name.

### What is still unverified

- **No new Z-codes.** A `pins`/`pin_defs` mismatch and a symbol-only component
  whose pads Ohm cannot see are both worth diagnosing, and neither is diagnosed:
  a new code needs an entry in `ohm/diagnostics.py`'s `EXPLAIN` table (enforced by
  `tests/test_p10.py`), which was owned by concurrent work when this landed. Both
  cases degrade safely today — an unreadable pad simply stays `None`.
- **`pin_defs` is undocumented.** The published spec
  (`docs/pages/spec.mdx`, checked 2026-08-31) lists `symbol` as required and never
  mentions `pin_defs`. It is what the implementation asks for by name in its own
  diagnostic, and `tests/test_zen_pcb.py` proves 0.4.41 accepts it — a checked
  fact, not a documented one, and it could move.
- **Only `pcbc build` is exercised**, on the `0.4` lane. Layout, BOM, DRC and the
  `0.3`/`nightly` lanes are untouched, and `pcbc build` cannot run at all on a
  Windows host without Developer Mode (it materialises its stdlib through a
  symlink and dies with `os error 1314` before reading a line of Zener) — so on
  such a host the gate skips rather than certifying anything.
- Decision 4's documented losses are unchanged: `Power(voltage=...)` annotations,
  `interface` definitions and config expressions still do not survive the trip.
