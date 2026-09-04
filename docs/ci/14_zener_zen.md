# 14 · Zener `.zen`, both directions

Loops 16-17 · 2026-08-31 · commit `a0eb5d1`

---

## What it was

ADR-002 (July 2026) shipped with its section 5 deferred: "the optional `pcb`
toolchain cross-check is deferred until the `pcb` binary is available locally." It
stayed deferred for a year. In that time `ohm/emit_zen.py` wrote

```python
Component(name="U1", footprint="SOT-23-5", prefix="U", pins={...})
```

and called it "the universal `Component()` form." A year of round-trip tests
proved only that Ohm's own importer agreed with Ohm's own exporter — never that
either agreed with Zener.

**diodeinc/pcb 0.4.41** (released 2026-08-31) was obtained and run — the first
time this project has had a real `pcb` binary at all. It disagrees with that
"universal" form immediately:

```
Either `pin_defs` or a Symbol value for `symbol` must be provided
[bom.unspecified] Component 'U1' is included in the BOM but is missing part
information. Specify `part=Part(...)`.
```

That closes ADR-002 §5's deferral, and closing it cost an export rewrite.

## What changed

`ohm/emit_zen.py` now writes, for example:

```python
Component(
    name="U1",
    footprint="SOT-23-5",
    prefix="U",
    pin_defs={"SCL": "5", "SDA": "6"},
    pins={"SCL": ..., "SDA": ...},
    part=Part(mpn="...", manufacturer="..."),
)
```

**The spec was not the authority — running `pcbc` was**, and it disagreed with the
published spec in ways that mattered:

- The old form fails both ways shown above.
- `pin_defs` is **undocumented in the published spec** — the tool named it in its
  own diagnostic, not in the spec's own `Component()` reference.
- Every pin named in `pin_defs` must also be connected in `pins`, so `emit_zen`
  builds both dicts from the one wired-endpoint list (`ohm/emit_zen.py:142-147`)
  rather than risking the two drifting apart.
- `part=Part(mpn=, manufacturer=)` needs both fields non-empty; the bare `mpn=`
  kwarg is accepted but deprecated, and `properties["mpn"]` is rejected outright as
  "no longer supported."
- `Component name cannot contain invalid characters "."` — Ohm's hierarchical
  refs (`mcu.u`) are illegal Zener instance names.
- `pcb fmt` disagrees with the published spec's own examples on kwarg whitespace
  (`name = "U1"` in the spec vs the formatter's own `name="U1"`).

**`symbol`:** Ohm cannot supply one honestly. `emit_zen` receives IR-LOW — parts,
nets, pins, pads, **no schematic geometry** — and writes no `.kicad_sym` beside its
output, so `symbol=Symbol("X.kicad_sym")` would be a reference resolving on no
machine anywhere. `pin_defs` carries the same pin→pad map instead
(`ir.NetNode.pad`, falling back to the pin's own name where unmapped — the same
fallback `emit_netlist.py` already applies), which is data Ohm actually has.

Where only `mpn` is known — the common case, since the language has no required
`manufacturer` — the legacy `mpn=` kwarg is emitted alone and the file keeps a
real `bom.underspecified` error. Deliberate: it is the identical gap Ohm's own
M001 reports about the identical design, and suppressing it with `skip_bom=True`
would silence a true finding rather than hand the downstream tool the same
complaint honestly.

`ohm/imp_zen.py` now accepts **both surfaces side by side** — the legacy form Ohm
itself wrote until this loop, and the current 0.4.41 surface — for the reason
ADR-021 gives about the KiCad readers: it faces files other people wrote, not only
files Ohm itself produces. `pin_defs` now fills `ir.NetNode.pad`, which the
importer used to leave `None` on every node.

## What proves it

`tests/test_zen_pcb.py`, 8 tests, driving the real toolchain:

- All 5 golden designs (`tests/golden/*.ohm`) export a `.zen` that
  `pcbc build -S bom` accepts, with the right component count (this parametrized
  test is 5 of the 8).
- A fully-sourced design (both `mpn` and `manufacturer` known) builds with **no
  suppression at all** — the proof that `-S bom` is scoping the BOM check, not
  hiding it.
- The old, pre-rewrite export form is still rejected with `pin_defs` named in the
  message — the finding that justified the rewrite, kept executable so a future
  `pcb` that starts accepting the old form again gets noticed.
- The emitted file is `pcb fmt --check` clean.

The **Windows** `pcbc` binary cannot build here at all: it materialises its stdlib
through a symlink and dies with `os error 1314` ("a required privilege is not held
by the client") before reading a line of Zener — a fact about this host's
privilege level, not about Ohm's output. Per the commit message that landed this
work, the **linux-musl** build of `pcbc` 0.4.41 was run in Docker to get real
results — 0.4.41 ships prebuilt for linux x86_64 gnu/musl, macOS aarch64 and
Windows x86_64 (`docs/adr/ADR_002_zen_interop_subset.md:36-37`). The guard
(`_unusable()`) probes with a hand-written, known-good component first, so a skip
means "the toolchain could not run here," never "Ohm's output went unchecked."

## Still not proven

- **No new Z-codes.** A `pins`/`pin_defs` mismatch and a symbol-only component
  (one that supplies `symbol=` instead of `pin_defs=`, for which `imp_zen`
  deliberately records no pad) both deserve a diagnostic of their own — every code
  needs an `EXPLAIN` entry in `ohm/diagnostics.py`, and that file belongs to
  another workstream this round. Both cases degrade safely today: pad stays
  `None` rather than crashing or silently misconnecting.
- **`emit_zen` names parts by `part.ref`, not `part.designator`.** `_refname`
  in `ohm/emit_zen.py` means a `.zen` export identifies a part as `mcu_u` where
  Ohm's own netlist calls the same part `U1` — while `imp_zen.py`'s own comment
  says "`.zen` instance names ARE designators." Pre-existing, left alone this
  loop, and flagged here rather than silently inherited.
- **No confirmation that 0.4.41's behaviour is representative of the `.zen`
  ecosystem generally** — only that it is representative of this one released
  version, obtained and run on 2026-08-31.

---

## Reproducing the toolchain gate

The `pcb` binary is not committed — it is a 41 MB third-party executable and a
13 MB standard library, and vendoring either would make Ohm's tests depend on
bytes Ohm does not own. So the gate skips by default, and this is how to make it
run. **Independently re-verified 2026-08-31: 8 passed.**

```sh
# 1. Fetch the toolchain (linux/amd64; also aarch64, macOS and Windows builds)
curl -sL -o pcbc  https://github.com/diodeinc/pcb/releases/download/v0.4.41/pcbc-x86_64-unknown-linux-musl
curl -sL -o stdlib.tar.zst https://github.com/diodeinc/pcb/releases/download/v0.4.41/stdlib.tar.zst

# 2. Lay it out the way pcbc looks for it. THIS IS THE STEP THAT BITES: pcbc
#    resolves its standard library relative to the binary and wants an ancestor
#    containing lib/std/pcb.toml. Extract into lib/std, NOT lib.
mkdir -p /opt/pcb/lib/std && cp pcbc /opt/pcb/ && chmod +x /opt/pcb/pcbc
tar --use-compress-program=unzstd -xf stdlib.tar.zst -C /opt/pcb/lib/std
test -f /opt/pcb/lib/std/pcb.toml   # the file pcbc actually looks for

# 3. Run the gate
OHM_PCB=/opt/pcb/pcbc python -m pytest -q tests/test_zen_pcb.py
```

Get step 2 wrong and the guard reports the tool's own words rather than a
generic failure, which is the whole point of probing before running:

```
SKIPPED: pcb present but cannot build in this environment: Error: could not find
stdlib source next to /opt/pcb/pcbc; expected an ancestor containing lib/std/pcb.toml
```

A skip that says *why*, in the tool's vocabulary, is the difference between "the
toolchain could not run here" and "Ohm's output went unchecked". Only the first
is acceptable, and the guard makes which one it is unambiguous.

**There is no CI job for this** — it was out of scope for this round by decision,
and the tool is a moving target on a lane (`0.3`, `0.4`, `nightly`) rather than a
stable pin. The gate is local-and-honest rather than continuous, which is a
weaker guarantee than the KiCad matrix and is stated as such.
