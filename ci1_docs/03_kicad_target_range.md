# 03 · `--target-kicad`, the supported range, and environment discovery

Loops 3-4-7, as reshaped by the KiCad 7 correction · 2026-08-31

---

## What the flag means

The most misread thing in this area, so it is stated first:

> **`--target-kicad` names the KiCad application you are targeting, not a
> serialisation.** Ohm emits the KiCad **7 file format** for every value of the
> flag, on every target, forever.

The two are independent. Ohm relies on KiCad reading **forward**, which is the
direction KiCad guarantees; a newer stamp would buy nothing and cost every older
KiCad the ability to open the file (ADR-021 section 1, and
[`06_emit_kicad.md`](06_emit_kicad.md)).

What the flag *does* name is the **library vocabulary**.
`ohm.lower._pad_translator` writes the target version's spelling of a pad id into
the netlist, so `pad=11` emits `11` on KiCad 7, 8 and 9 and `SH` on KiCad 10 —
from one source. Before that existed, the flag emitted byte-identical output for
every value: it named nothing.

## The supported range

`ohm/cli.py:106` — `SUPPORTED_KICAD = ("7", "8", "9", "10")`. **Unchanged, and
that is itself a decision this round took and then reversed.**

The round opened by raising the floor to 8, on ADR-021's own recommendation. The
reasoning: KiCad 7 could not be measured — no container image, therefore no CI
leg, therefore no pad data, therefore `--target-kicad 7` was *unchecked* rather
than *checked and clean*. That work was implemented in full (`cli.py`,
`project.py`, `kicad_env.py`, `lower.py`, `check_structural.py`, the scaffold
template and seven test modules) and then **reverted**, because the premise was
false. `kicad/kicad:7.0.11` exists. See
[`10_container_tag_currency.md`](10_container_tag_currency.md).

What replaced it is better than either position: KiCad 7 is neither dropped nor
unchecked. It is a measured matrix leg — 337 tests, 0 failed, 0 skipped — with
its own pad data in the shipped table.

The reversal is recorded rather than tidied away, in ADR-021's own
`Correction - 2026-08-31` section and in the four other documents that carried
the claim. A later reader who wonders why the floor is 7 should find the argument
for 8 *and* the reason it does not hold, not a repo that reads as if the question
was never asked.

## Environment discovery — `ohm/kicad_env.py`

The single "where is KiCad" resolver. Everything routes through it: `ohm doctor`,
`ohm verify-footprints`, `ohm verify-pinouts`, and the whole suite's guards
(`tests/conftest.py:36-42`, which replaced five hardcoded `/usr/share/kicad/...`
copies).

Its properties, unchanged this round but load-bearing enough to record:

- **Newest first.** `_MAJORS = ("10", "9", "8", "7")`, and both `_ENV_TEMPLATES`
  and the install prefixes are *derived* from it — so the supported range is one
  number in one place, and `tests/test_kicad_env.py` keys its coverage test off
  `SUPPORTED_KICAD` to prove the two cannot drift. When the floor-to-8 change was
  made and later reverted, this file needed exactly one edit in each direction,
  which is the property working.
- **Environment beats probing.** `KICAD10_FOOTPRINT_DIR` through
  `KICAD_SYMBOL_DIR` are checked before any filesystem probe, because an
  explicitly set variable is a statement about which KiCad this machine means. A
  variable pointing at a non-directory is ignored rather than trusted.
- **`$OHM_KICAD_CLI` wins over `PATH`.** This is what lets the same gate be
  pointed at a KiCad that is not installed — `tests/kicad_in_docker.sh` is a shim
  that runs `kicad-cli` inside a container, and it is how one machine certified
  four versions.
- **No `subprocess`.** Only `stat` and `shutil.which`. Hermeticity (N1) forbids
  the compiler shelling out, and this module is reachable from the build path.

`ohm doctor` reports missing libraries as a **warning, never a failure** — they
are needed only to open an emitted `.kicad_pcb`, not to compile.

## Still not proven

- A KiCad installed outside every probed prefix is found only through the
  environment variables. There is no Windows registry read and no
  `kicad-cli --version` discovery, both of which would need I/O this module is
  not allowed to do.
