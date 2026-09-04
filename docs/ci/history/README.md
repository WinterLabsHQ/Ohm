# The three documents this record grew out of

**Superseded. Kept for provenance, not for reference.** The current state of
every claim these make is [`../README.md`](../README.md) and the files beside
it; where the two disagree, the current record wins by construction, because it
was measured later.

They sat at the repository root as `KICAD_COMPATIBILITY.md`,
`KICAD_CI_PLAN.md` and `KICAD_CI_IMPLEMENTATION.md`, which put three superseded
documents in the first thing anyone sees. They are here instead, renumbered into
the order they were written, because they are still cited by name — from
`ohm/emit_kicad.py`, from `tests/test_kicad_plot.py`, and from a dozen places in
the current record that refer to a finding by its number.

| | Written | What it is |
|---|---|---|
| [01](01_audit_kicad_7_to_10.md) | 2026-08-26 | **The audit.** Every KiCad-version claim in the repo, graded `proven` / `assumed` / `untested` / `broken`, with file:line. Findings **F1–F8** — the numbering the rest of the record still uses. |
| [02](02_ci_feasibility_plan.md) | 2026-08-26 | **The plan.** Could the `assumed` cells be turned into proof by CI, what had to change, what it would cost. A specification; it changed no code. |
| [03](03_ci_implementation.md) | 2026-08-27 | **What was built**, and the five real defects that running it found. |

## Why they are kept rather than deleted

Two of the three are wrong in places, and that is the point.

Document 02 asserts in four places that no official KiCad container image exists
below 8.0.4. It does not: `kicad/kicad:7.0.11` has been on Docker Hub since
February 2024, and the mistake was reading one page of a tag listing ordered by
last-push date. That single false premise set a support floor, reached five
other documents, and stood until `tools/check_kicad_tags.py` paginated the whole
list. It is corrected **in place and marked** rather than rewritten to look as
though the error was never made, and [`../10_container_tag_currency.md`](../10_container_tag_currency.md)
is the record of finding it.

Deleting a document because it turned out to be wrong deletes the evidence of
how the wrong thing was believed — which is the part worth keeping. A reader who
wants only what is true today should be in [`../README.md`](../README.md); a
reader who wants to know how a claim came to be trusted needs these.
