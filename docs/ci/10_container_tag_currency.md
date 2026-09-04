# 10 · Container-tag currency — a pin is only as honest as it is current

Loops 11 and the KiCad 7 correction · 2026-08-31 · commits `d299c9a`, `5cd9415`

---

## What it was

Ohm's KiCad support range is a **measurement**, not a claim: the matrix in
`.github/workflows/ci.yml` runs the gates inside `kicad/kicad:<tag>` at each
supported major. That design is only as good as the tags it names, and nothing
in the repo ever checked them.

Two things were wrong with the pins at once, in opposite directions.

## Finding 1 — a version that exists has no image

**KiCad 10.0.6 was released in August 2026. `kicad/kicad:10.0.6` returns HTTP
404.** The newest containerised release is still 10.0.5.

The stable release and the newest containerised release are **different numbers**,
and only the second one can be a gate. So `10.0.5` is not a stale pin — it is
the correct pin, and the ceiling of what is provable. But nothing recorded that,
and nothing would have noticed the day the image appeared.

## Finding 2 — a version that has images was declared impossible

The repo asserted, in **five** places, that no official KiCad container image
exists below 8.0.4:

- `history/02_ci_feasibility_plan.md` — "**KiCad 7 cannot be containerised.** The official
  `kicad/kicad` image starts at 8.0.4. There is no 7.x tag."
- `history/03_ci_implementation.md` — "No official container image exists below
  8.0.4, so 7 is certified by hand or not at all."
- `docs/adr/ADR_021_kicad_version_policy.md` — twice, including the consequence
  "**KiCad 7 is the weak leg** … the honest move is to raise the floor to 8."
- `README.md` — "KiCad 7 has no published container image, so it is verified by
  hand and carries no footprint-pad data."
- `tests/kicad_in_docker.sh` — a hard `exit 2` guard rejecting any `7.*`.

**All false.** `kicad/kicad` carries `7.0`, `7.0.8`, `7.0.9`, `7.0.10` and
`7.0.11`. `kicad/kicad:7.0.11` is linux/amd64, 439.9 MB, pushed 2024-02-23. And
`Dockerfile.7.0-stable`, in the same `gitlab.com/kicad/packaging/kicad-cli-docker`
repository that builds the 8, 9 and 10 images, does exactly what the 8/9/10 ones
do:

```dockerfile
FROM debian:bookworm AS build
ARG KICAD_VERSION=7.0
RUN git clone -b $KICAD_VERSION …/kicad-symbols.git; \
    git clone -b $KICAD_VERSION …/kicad-footprints.git;
```

So KiCad 7 is containerised **with version-matched libraries** — the exact
property the whole pad-provenance strategy depends on — on the same Debian 12
base as 8 and 9.

### How the error happened

Docker Hub's tag listing is ordered by `last_updated` and returns 50 rows per
page. KiCad 7.x was last pushed in **February 2024**, so it is not on page one.
Whoever checked looked at the first page and generalised. It is an easy mistake
and it survived four documents, an ADR, the README and a shell guard, and it
came within one loop of removing a supported version from the product.

### What it cost, and what it bought

The false premise had already produced a decision: raise the supported floor to
8. That decision was taken, implemented, and **reverted** when the tags were
checked. `SUPPORTED_KICAD = ("7", "8", "9", "10")` is unchanged.

What the correction bought, measured the same day:

- **KiCad 7.0.11 certifies clean** — 337 tests at this loop, 0 failed, 0
  skipped, under
  `OHM_REQUIRE_KICAD=1`, with no step excluded. See
  [`21_compatibility_matrix.md`](21_compatibility_matrix.md).
- **KiCad 7 pad data now exists**, closing the last case where `fpdata` had to
  abstain on a version Ohm advertised as supported.
- Having that data immediately found a real defect in Ohm's own standard library
  and a misleading diagnostic — see [`05_lower_and_fpdata.md`](05_lower_and_fpdata.md).

---

## What changed

**`tools/check_kicad_tags.py`** — paginates the *whole* tag list (116 tags, not
50), keeps only bare `X.Y.Z` release tags, and per major reports the newest image
against the pin:

```
$ python tools/check_kicad_tags.py 7.0.11 8.0.9 9.0.9 10.0.5
CURRENT  7.0.11 — newest image for major 7
CURRENT  8.0.9 — newest image for major 8
CURRENT  9.0.9 — newest image for major 9
CURRENT  10.0.5 — newest image for major 10
```

Exit 0 = every pin current. Exit 1 = a newer image exists, naming what to bump
to and why it matters (*"a KiCad release is exactly when a library rename
lands"*). Exit 2 = a pin does not exist at all, or the registry could not be
read — the case where the matrix names an image nobody can pull and the job
would silently not have run.

Rolling tags (`10.0`, `nightly`) are deliberately excluded: the bytes behind them
change without the pin changing, which is the whole failure this file exists to
stop.

Network I/O, so it lives in `tools/` and never on the build path — the compiler
is hermetic (N1). Stdlib-only, like `tools/gen_fpdata.py`, so it runs on a
container interpreter with no venv.

**The five documents** are corrected in the house style already established by
`history/03_ci_implementation.md`'s "Corrections to the plan, forced by measurement"
section: struck through in place and annotated with the evidence and the date,
never silently rewritten. `history/01_audit_kicad_7_to_10.md` is deliberately untouched — it
carries its own banner saying it is kept unedited as the record of what was
believed before anyone checked.

**`.github/workflows/ci.yml`** gains a `tags` job on the same non-PR trigger as
the KiCad matrix, so a KiCad release does not block an ordinary pull request. It
is deliberately **not** in the `certify` chain: a new upstream release must not
invalidate a certification of what actually ran.

---

## Still not proven

- The `tags` job runs against the live registry, so it can fail for a network
  reason rather than a drift reason. Exit 2 distinguishes them, but the job
  treats both as failure.
- Nothing checks that a *newly appeared* image is one Ohm should adopt. The job
  reports drift; a human decides whether to bump, re-certify and regenerate the
  pad table.
