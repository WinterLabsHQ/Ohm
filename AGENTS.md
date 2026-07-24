# Ohm for agents

Ohm is designed to be authored by LLM agents. The contract:

1. **Read [`docs/STYLE.md`](docs/STYLE.md) first.** It is self-contained — file
   shape, interfaces, roles, kinds, `expects`, operating points, placement.
   Do not guess syntax from other HDLs; `.ohm` is not Starlark or Python.
2. **Iterate on machine diagnostics.** Run `ohm check <file> --json-diagnostics`
   and fix what it reports; every code is explained by `ohm explain <CODE>`.
   One diagnostic round typically reaches clean (measured in our live-LLM
   fixtures — see `examples/io_expander_board.ohm`).
3. **Declare, don't decorate.** Diagnostics scale with declared intent: use
   typed interfaces (`io(I2C)`) over raw signals, declare `kind` conformance
   on components, declare pin operating points (`voltage=`, `current=`).
   Undeclared values are honestly unchecked — never invent numbers.
4. **Waive visibly.** If a check does not apply, use `waive="..."` on the
   instance — it becomes an auditable note (B010). Never suppress by
   restructuring the design to hide intent.
5. **After routing, run `ohm signoff <src.ohm> <routed.kicad_pcb>`** and treat
   V-errors like compile errors: fix the board (or the source) until exit 0.

Repo hygiene for coding agents: `PROGRESS.md` is the execution log,
`docs/blueprint/` is read-only canonical, tests gate everything
(`pytest -q`, `ruff check .`, `mypy ohm`), builds must stay hermetic and
deterministic (the suite enforces both).
