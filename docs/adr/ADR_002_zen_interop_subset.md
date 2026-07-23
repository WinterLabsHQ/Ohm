# ADR-002 — .zen interop: bounded Starlark subset via Python `ast` (P7)

Date: 2026-07-23 · Status: accepted

## Context
`.zen` (Zener, diodeinc/pcb) files are Starlark modules — and Starlark syntax is a
subset of Python syntax by design. The blueprint mandates `.zen` as import/export
interop only (D1/N5), never a build dependency.

## Decision
1. **Parse `.zen` with Python's stdlib `ast`** — no Starlark interpreter dependency;
   the interop path stays pure-Python and hermetic.
2. **Bounded, documented subset** (matching the published Zener spec):
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
   never wired into `ohm build`.

## Consequences
Zero new dependencies; the subset is honest about what survives the trip; a full
Starlark evaluator can replace the `ast` walker later without touching the IR side.
