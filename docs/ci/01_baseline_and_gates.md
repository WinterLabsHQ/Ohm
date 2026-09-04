# 01 · Baseline and gate hygiene

Loop 1 · 2026-08-31 · commits `78338e6`, and this one

The floor everything else is measured against. Nothing here changes what Ohm
emits; it changes what the suite is able to notice.

---

## The baseline

44 modified files and 10 new ones were sitting uncommitted in the working tree —
the whole KiCad 8/9/10 upgrade described in `history/02_ci_feasibility_plan.md` and
`history/03_ci_implementation.md`: `ohm/kicad_env.py`, `ohm/fpdata/` + S012,
`ohm verify-footprints`, the signoff read-back and plot gates, and the five-job
CI matrix. Written, measured and documented, but never committed, so there was no
floor to diff against.

Landed unchanged on branch `ci1-eda-upgrade` as `78338e6`, with one fix.

**Measured baseline: 889 passed, 83 skipped, ruff and mypy clean, on Windows /
CPython 3.12.13.** (`history/03_ci_implementation.md` records 888/83 on Linux; the
difference is the test added below.)

---

## What running it on Windows found

`tests/test_quant.py::test_q_checks_deterministic` **failed**, and had presumably
been failing on Windows for as long as it has existed. It spawns a fresh
interpreter per hash seed — the point being that two in-process runs can never
catch set/dict iteration-order nondeterminism — via `python -c`, which never goes
through `ohm.cli.main()`. `main()` is the one place the UTF-8 stream forcing
lives (`ohm/cli.py:433-439`), so a cp1252 console died printing Q003's `ΔV=`:

```
UnicodeEncodeError: 'charmap' codec can't encode character 'Δ'
```

The run failed on console encoding rather than on the nondeterminism the test
exists to catch. Fixed by asking the subprocess for UTF-8, which is what CI
already does (`ci.yml` sets `PYTHONUTF8: "1"`).

**That left the actual claim untested.** The architecture document states, as an
invariant, that "the console streams are forced to UTF-8 so a cp1252 terminal
cannot crash on a `ΔV` in a diagnostic". It was asserted in a comment and proved
by nothing. It now has its own test:

```python
def test_cli_survives_a_non_utf8_console():
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
    r = subprocess.run([sys.executable, "-m", "ohm", "check", str(fixture)], ...)
    assert b"UnicodeEncodeError" not in r.stderr
```

This matters beyond tidiness: **Loop 22 adds a Windows CI leg**, and it would
have gone red on day one for a reason that has nothing to do with what it is
measuring.

---

## The skip that would have failed the certification

`tests/test_pkggen.py` drives `kicad-cli sym upgrade`, and skips when the command
is not available:

```python
pytest.skip(f"kicad-cli 'sym upgrade' unsupported here: ...")
```

Under `OHM_REQUIRE_KICAD=1` — the switch that makes the matrix evidence rather
than decoration — `tests/conftest.py:140-166` converts any skip whose reason
contains `"kicad"` or `"pcbnew"` into a **failure**. This reason contains
`kicad-cli`. The `NOT_APPLICABLE` sentinel exists for exactly this case (a
capability a supported KiCad genuinely lacks, not a broken environment) and was
not used, so the certification run would have failed on the one class of thing it
is explicitly designed not to fail on. Sentinel added.

---

## Tests that ran nowhere

Three test modules existed, passed locally, and were in **no** CI job's file
list — so their KiCad-dependent half executed on no machine at any version:

| Module | Ran in `fast`? | Ran in `kicad`? |
|---|---|---|
| `tests/test_verify_footprints.py` | skipped (no KiCad) | **not listed** |
| `tests/test_kicad_env.py` | passed (synthetic trees) | **not listed** |
| `tests/test_fpdata.py` | passed (shipped table) | **not listed** |

The two `@requires_footprint(SOIC8_MOD)` tests at `test_verify_footprints.py:260`
and `:273` therefore proved nothing anywhere. `test_verify_footprints.py` joins
the **library gates** step, since it reads the real installed library. The other
two get a new step of their own — they pass without KiCad, but they are the code
that *decides* which KiCad the rest of the suite talks to and which pad ids it
expects, so a resolver that answers correctly on a bare runner and wrongly in
front of a real installation should not be able to hide.

This is the same failure mode `history/03_ci_implementation.md` records under finding
3 — a golden comparison that had never executed on any machine at any version.
It was not a one-off.

---

## Configuration

**`pyproject.toml` had no pytest configuration at all** — no `testpaths`, no
marker registration, no strictness. Added:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--strict-markers --strict-config"
xfail_strict = true
```

`--strict-markers` makes a typo'd `@pytest.mark.foo` an error rather than a
silent no-op; `xfail_strict` makes an xfail that starts passing **fail**, so a
fixed bug cannot leave behind a permanently-green test that proves nothing.

**`tools/` was covered by neither linter.** It is author-time acquisition, off
the build path — but it writes the pad table `ohm check` reads (ADR-021), so it
is gated like everything else. `mypy` `files` now includes it; `ruff` in
`ci.yml:28` now names it. Both were already clean, which is why this is cheap
insurance rather than a cleanup.

---

## Verify

```sh
.venv/Scripts/python -m pytest -q                                  # 889 passed, 83 skipped
.venv/Scripts/python -m ruff check ohm/ shared/ tests/ tools/      # All checks passed
.venv/Scripts/python -m mypy                                       # 57 source files, no issues
```

## Still not proven here

- The `OHM_REQUIRE_KICAD` conversion of the `sym upgrade` skip is fixed by
  inspection of the sentinel logic, not by a certification run — no KiCad is
  installed on this machine. Loop 24 executes the matrix.
- The three newly-wired test modules are wired in YAML that has still never run
  on a GitHub runner. Also Loop 24.
