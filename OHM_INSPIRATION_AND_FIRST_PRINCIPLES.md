# OHM — INSPIRATION & FIRST PRINCIPLES (read at the start of every session)

> **Purpose of THIS document.** The other docs say what to build (`ENGINEERING_BUILD_MANUAL`),
> how the compiler works (`COMPILER_DESIGN`), and which decisions were made (`MASTER_BUILD_
> DOCUMENT`, `ADR_001`). This one says **WHY** — the north star and the reasoning behind the
> non-negotiables. It exists because across many sessions, context gets compacted and early
> reasoning is lost; an agent then "helpfully" simplifies away the very things that make Ohm
> worth building. **If you are an agent starting a session: read this first. When a change
> feels convenient but contradicts something here, this document wins — stop and reconsider.**

---

## 1. The one-sentence reason Ohm exists

> Traditional circuit tools verify that a design is **connected correctly**; they cannot
> verify that it is **what the designer meant** or that it is **physically buildable**.
> Ohm makes *intent* and *physical realizability* first-class, so entire classes of errors
> are caught at author time — by a language that is equally natural for a human and an AI.

If a proposed change does not serve that sentence, it is probably wrong.

---

## 2. Where the inspiration came from (the lineage, so it is never lost)

Ohm stands on a clear line of prior art, and it exists because each predecessor left a
specific gap:

- **KiCad / traditional schematic capture** — graphical, powerful, but designs live as
  drawings, not code: no version control that means anything, no reuse, no programmatic
  validation beyond connectivity ERC. Inspiration: hardware deserves the tooling software
  has.
- **SKiDL** — proved circuits can be described as code (Python), with ERC and netlist
  output. Gap: it is a library, not a language; it catches only connectivity-tier mistakes.
- **Zener (Diode)** — a real, type-safe, Starlark-based HDL with typed interfaces and
  optional checks; LLM-friendly by design. Gap (the one that started this project): its
  checks are *opt-in*, its wire is *ideal* (no physical realizability), and building ON it
  makes us a wrapper, not a platform.
- **atopile** — the closest sibling: a declarative `.ato` language + compiler with modules,
  interfaces, units, tolerances, and assertions; it captures intent with equations. It
  validated our core bets (custom grammar, own compiler, intent capture, open-source
  language). Ohm goes further on *physics-aware realizability* and on being *AI-native by
  construction*.
- **tscircuit** — code-first, renders schematic+PCB, keeps simulation an explicit step.
  Independent confirmation of our closed-form/convergence line: simulation is not fused into
  the build.

**The synthesis that is Ohm:** take code-first hardware (SKiDL/atopile/tscircuit lineage),
add a *type system for intent* (so miswires and role errors are caught), add *physics-aware
realizability* (so unbuildable designs are caught), keep it *hermetic and reproducible*, and
make it *first-class for AI agents and humans alike*. No predecessor did all of these at once.

---

## 3. The founding intuitions (the human insights that shaped it)

These came from real reasoning during design. Preserve them; they are the "taste" of the
project.

1. **"If the design is a complete loop, we could detect these too."** → became the
   detectability principle: a mistake is catchable iff the program contains both what was
   BUILT and what was DECLARED-MEANT. Detectability grows by making *intent declarable*, not
   by writing more ad-hoc checkers.
2. **"We can't build our product on another company's product — credibility and control."**
   → became Own The Core (ADR-001): our own compiler, IR, emitters; `.zen` is interop only.
3. **"Physics should not let fundamental design mistakes pass."** → became physics-aware-by-
   design, but disciplined: only the *safe* (closed-form, deterministic) physics lives in the
   compiler; the dangerous (convergent) physics is delegated. The instinct was right; the
   discipline is what makes it survivable.
4. **"It must feel like declaring a variable or a function."** → became the ergonomics rules
   and, crucially, the insight that *ergonomics and safety must be aligned* — the easy path is
   the checkable path, never a trade-off.
5. **"Friendly for both humans and agents."** → not a feature, a constraint on every design
   choice: syntax shape, error messages (dual audience), determinism (so agents can rely on
   reproducible feedback), free declaration order.
6. **"KiCad's library is tiny and building a part pin-by-pin is hectic — generate the symbol
   and footprint for any component."** → became package generation, split along the SAME safe/
   dangerous line as physics and ratings: *generation* from known pin/geometry data is
   deterministic, IPC-7351-formula-driven, and lives in the compiler; *acquisition* of that
   pin data (datasheet/API/LLM) is best-effort, happens at authoring time, and is frozen into
   the source before the build. The instinct (remove hectic manual work) was right; the
   discipline (never fetch in the build, never claim perfect auto-inference) is what keeps it
   from violating hermeticity and honesty.

---

## 4. THE NON-NEGOTIABLES (and the exact failure that happens if each is violated)

This is the heart of the document. Each principle is paired with the seductive-but-wrong
change that a future session might make, and the damage it causes. **Recognize the
temptation; refuse it.**

### N1 — `ohm build` is hermetic and reproducible. No I/O, network, clock, or solver. Ever.
- *Tempting wrong move:* "It'd be handy to fetch live part data / run a quick simulation
  during build."
- *Why it's fatal:* the build stops being reproducible; the same source yields different
  outputs on different machines/commits. Git-diffability dies, every downstream oracle dies,
  and agents can no longer trust build feedback. This is the single most important invariant.

### N2 — The closed-form vs convergence line is enforced by GRAMMAR, not discipline.
`assert` = closed-form, evaluated at build. `obligation` = convergent, becomes a Hole,
delegated to the solver layer, never evaluated in-compiler.
- *Tempting wrong move:* "Let `assert` also handle this SPICE/field check — it's almost the
  same."
- *Why it's fatal:* it reintroduces convergent physics into the build, breaking N1. The two
  forms are categorically different on purpose so this cannot happen by accident. Keep them so.

### N3 — Detectability claims are bounded by declared intent. Never "catches all mistakes."
- *Tempting wrong move:* marketing or docs that imply Ohm catches design-judgment errors
  ("wrong IC for the job", "filter cutoff wrong for the application").
- *Why it's fatal:* the first time it fails to catch such a thing, credibility collapses —
  and credibility is a stated core value of this company. Claim exactly: *"the more intent you
  declare, the more Ohm catches."*

### N4 — Ergonomics and safety are aligned; reject syntax where the easy path is less safe.
- *Tempting wrong move:* "Let people connect raw pins quickly as the default; typed
  interfaces are more ceremony."
- *Why it's fatal:* it removes the declared intent that makes Tier-I detection possible. The
  friendly path MUST be the typed path (`mcu.i2c ~ sensor.i2c`), which is also *less* typing.
  Raw pin connection stays only as an explicit escape hatch.

### N5 — The IR is ours and is the crown asset. Never let an external tool own it.
- *Tempting wrong move:* "Just emit to `pcb`/another tool's format as the primary path and
  skip our own netlist emitter."
- *Why it's fatal:* it silently rebuilds the wrapper we rejected in ADR-001; control and
  credibility leak back out. Own the representation end to end.

### N6 — Open/closed boundary = representation (open) vs hard computation (closed).
Language + reference compiler = open. Meridian + physics solver + ML + platform = proprietary.
- *Tempting wrong move:* "Fold a bit of the router/physics into the open compiler for
  convenience," or "close part of the language to protect it."
- *Why it's fatal:* closing the language kills the adoption that is its whole point (the
  moat is network effects); leaking proprietary computation into the open compiler gives away
  the actual moat. The open compiler must never import proprietary code.

### N7 — Compiler passes are pure `(ir, ctx) -> (ir, diagnostics)`; checkers never mutate IR
or halt the pipeline.
- *Tempting wrong move:* "Just mutate the IR in place here / bail out on the first error —
  simpler."
- *Why it's fatal:* it breaks determinism, breaks the run-to-completion "show all errors at
  once" behavior agents and humans depend on, and makes passes untestable in isolation.

---

## 5. What Ohm is NOT (scope guards, so sessions don't drift)

- **Not a simulator.** It declares obligations; the separate solver layer simulates. If you
  are writing convergence numerics inside the compiler, stop — wrong layer.
- **Not a router.** Meridian is a separate proprietary build. Ohm only emits the constraint
  schema Meridian consumes. Do not start routing here.
- **Not a Zener superset (anymore).** That was an earlier idea, dropped in ADR-001. `.zen` is
  import/export interop. Do not re-anchor the architecture on Zener/`pcb`.
- **Not a general-purpose language.** It is a domain language for describing circuits/PCBs
  with intent and physical realizability. Resist feature creep toward general programming.
- **Not a datasheet scraper.** Ohm *generates* KiCad symbols/footprints from pin/geometry data
  that is declared or frozen into the source (deterministic, IPC-7351). It does NOT fetch or
  infer pin data during the build. If you are adding a network call or a PDF/LLM pinout reader
  to the build path, stop — acquisition belongs at authoring time, frozen, never in `ohm build`.

---

## 6. The values behind the decisions (the "why we care")

- **Correctness over convenience.** Every non-negotiable trades a little convenience for a
  guarantee. That trade is the product.
- **Honesty over hype.** We claim exactly what we can do (N3). This is both an ethic and a
  moat: trustworthy tools win in hardware, where mistakes cost fabricated boards.
- **Own what defines us; share what spreads us.** Own the representation and the hard
  computation; open the language so it can become a standard.
- **Built for two authors at once.** Humans and agents are equal first-class users. A choice
  good for one but bad for the other is not done.

---

## 7. Session ritual (do this every time)

At the start of a session working on Ohm:
1. Read §1 (the one-sentence reason) and §4 (the non-negotiables).
2. Look at the current phase in `OHM_ENGINEERING_BUILD_MANUAL.md` and the matching passes in
   `OHM_COMPILER_DESIGN.md`.
3. Before making any change that feels like a shortcut, check it against §4. If it violates a
   non-negotiable, it is not a shortcut — it is a regression. Choose the principled path even
   if it is more work.
4. If a genuinely new decision is required that these documents don't cover, make it in the
   spirit of §6, and write it down as a new ADR so the next session inherits it.

---

## 8. One paragraph to reconstruct everything (if all else is lost)

Ohm is an AI-native, physics-aware hardware description language with its own hermetic,
reproducible compiler (Python + Lark), built because existing tools verify connectivity but
not *intent* or *physical realizability*. Its headline power is that design intent is
declarable (typed interfaces, roles, ratings, assertions), so miswires, role conflicts, and
unbuildable designs are caught at author time; its discipline is that only safe closed-form
physics runs in the compiler while convergent physics is delegated as obligations to a
separate proprietary solver layer. It also generates KiCad symbols and footprints
deterministically (IPC-7351) from pin data frozen into the source — solving KiCad's sparse-
library, manual-pin-by-pin pain — while never fetching or inferring that data during the
build. It owns its representation (the IR) and treats Zener `.zen` as interop only. The
language and reference compiler are open source to become a standard; the router (Meridian),
physics solvers, ML, and platform are the proprietary moat. Everything is designed to be
equally natural for humans and AI agents, and to be correct, reproducible, and honest about
its limits.
