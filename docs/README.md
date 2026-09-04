# Ohm — documentation

Four kinds of document live here, and they answer different questions. The
distinction is load-bearing: a decision record and a measurement record age in
opposite directions, and filing them together is how a repository ends up with a
"what is proven" page that nobody trusts.

| Folder | Question it answers | Ages by |
|---|---|---|
| [`STYLE.md`](STYLE.md), [`LSP.md`](LSP.md) | How do I **use** this? | being kept current |
| [`adr/`](adr/) | **Why** is it built this way? | accumulating — an ADR is superseded, never edited away |
| [`blueprint/`](blueprint/) | What was it **meant** to be? | not at all — read-only canonical |
| [`ci/`](ci/) | What has actually been **measured**? | being re-measured |

---

## Using Ohm

- **[`STYLE.md`](STYLE.md)** — the authoring guide, self-contained. File shape,
  interfaces, kinds, `expects`, operating points, placement. Written for humans
  and for agents; `AGENTS.md` at the repo root tells an agent to read it first.
- **[`LSP.md`](LSP.md)** — editor integration over stdio (`ohm lsp`).
- **[`OHM_ARCHITECTURE.pdf`](OHM_ARCHITECTURE.pdf)** — the architecture as a
  single diagrammed document.

## Decisions — [`adr/`](adr/)

Twenty-one records, ADR-002 through ADR-022, each stating a decision, the
alternatives, and what it costs. The ones most often cited from the code:

| | |
|---|---|
| [ADR-008](adr/ADR_008_kind_system.md) | `kind` — a checked component-type taxonomy |
| [ADR-011](adr/ADR_011_signoff.md) | `ohm signoff` — the routed board, verified against its source |
| [ADR-016](adr/ADR_016_hierarchical_sheets.md) | one schematic sheet per module |
| [ADR-017](adr/ADR_017_drawn_not_labelled.md) | a schematic is drawn, not labelled |
| [ADR-020](adr/ADR_020_multi_eda_targets.md) | native Altium and Allegro targets |
| [ADR-021](adr/ADR_021_kicad_version_policy.md) | one KiCad format out; provenance for what comes back |
| [ADR-022](adr/ADR_022_emitted_format_versions.md) | every version stamp Ohm writes, and the evidence for each |

## The original design — [`blueprint/`](blueprint/)

`OHM_COMPILER_DESIGN.md`, `OHM_ENGINEERING_BUILD_MANUAL.md` and
`OHM_INSPIRATION_AND_FIRST_PRINCIPLES.md`. **Read-only canonical.** They describe
the compiler that was designed; where the built compiler differs, an ADR says so
and the ADR wins. Nothing here is edited to match the code — that would destroy
the only record of what the code was supposed to be.

## What is measured — [`ci/`](ci/)

The compatibility and CI record: one file per part of the repository, each
stating what that part was, what changed, what now proves it, and **what is
still unproven**. Start at [`ci/README.md`](ci/README.md); the three summaries
worth reading on their own are:

- [`ci/21_compatibility_matrix.md`](ci/21_compatibility_matrix.md) — the matrix, as executed
- [`ci/22_latest_capabilities.md`](ci/22_latest_capabilities.md) — what Ohm can do that it could not
- [`ci/23_not_proven.md`](ci/23_not_proven.md) — what is still not proven

[`ci/history/`](ci/history/) holds the three superseded documents that this
record grew out of. They are kept because live code and live records still cite
their finding numbers — not because they are current.

---

## Where the rest lives

Not everything is a document, and the things below are deliberately not:

| | |
|---|---|
| `AGENTS.md` | the contract for agents authoring Ohm — repo root, read before `docs/` |
| `README.md` | what Ohm is, for someone who has not met it |
| `CHANGELOG.md` | what changed, per release |
| `PROGRESS.md` | the execution log — what was done, in order, with what it cost |
| `ohm/*.py` docstrings | **why this code is shaped this way.** The module docstrings carry the reasoning for the decision at hand; a document repeating them would be a second copy to keep true |
| `tests/test_*.py` | the claims. A property this project asserts and does not test is a property it does not have |
