"""The CI workflow, checked by the suite that it gates.

`.github/workflows/ci.yml` has never run on a GitHub runner
(`docs/ci/23_not_proven.md`), so every claim it makes and every path it names
has only ever been read by a human. These are the two failure modes that a first
run would otherwise discover the expensive way: a step that cannot execute at
all, and a step that names a file nobody wrote.

Deliberately stdlib-only and text-based. PyYAML is not a declared dev dependency
(`pyproject.toml`), and a gate that silently skips on a fresh checkout is the
failure `OHM_REQUIRE_KICAD` exists to prevent, one level down.
"""

import re
from pathlib import Path

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
REPO = Path(__file__).parent.parent


def _text():
    assert WORKFLOW.exists(), f"{WORKFLOW} is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def test_a_container_that_installs_packages_runs_as_root():
    """The kicad/kicad images set a non-root USER (`uid=1000(kicad)`), and the
    `kicad` job's first step is `apt-get update && apt-get install`.

    Measured on this machine against kicad/kicad:10.0.5:

        docker run --rm kicad/kicad:10.0.5 bash -c 'apt-get update'  -> exit 100
        docker run --rm --user root ...    bash -c 'apt-get update'  -> exit 0

    Every local reproduction of the matrix in this repo passes root explicitly,
    so the one path never exercised was the one that assumes it. A failure in
    that first step takes down all four matrix legs, `fpdata` and `certify`.
    """
    text = _text()
    seen = 0
    for m in re.finditer(r"^[ \t]*container:[ \t]*$", text, re.MULTILINE):
        seen += 1
        block = text[m.start():m.start() + 1200]
        assert "--user root" in block, (
            "a `container:` job does not declare `options: --user root`; the "
            "KiCad images run as uid 1000 and the job installs packages")
    # A regex that matched nothing would pass this forever — the same anti-vacuity
    # guard tests/test_p10.py carries, for the same reason.
    assert seen == 1, f"expected exactly one container job, found {seen}"


def test_no_container_is_declared_as_a_bare_string():
    """`container: image` cannot carry `options:`, so the root declaration above
    can only exist in the mapping form. Written down because collapsing it back
    to the string form is the natural "tidy-up" that would silently undo it."""
    text = _text()
    # `[ 	]`, not `\s`: `\s` matches the newline and would read the
    # `image:` line below a well-formed mapping as a bare string.
    bare = re.findall(r"^[ \t]*container:[ \t]+\S.*$", text, re.MULTILINE)
    assert not bare, f"container declared as a bare string: {bare}"


def test_every_test_file_the_workflow_names_exists():
    """A job that names a test file nobody wrote fails on the runner, not here."""
    text = _text()
    named = sorted(set(re.findall(r"tests/[\w./-]+\.py", text)))
    assert named, "no test paths found — has the workflow changed shape?"
    missing = [p for p in named if not (REPO / p).exists()]
    assert not missing, f"the workflow runs test files that do not exist: {missing}"


def test_every_tool_and_example_the_workflow_names_exists():
    text = _text()
    named = sorted(set(re.findall(r"(?:tools|examples)/[\w./-]+\.(?:py|ohm)", text)))
    assert named, "no tool/example paths found — has the workflow changed shape?"
    missing = [p for p in named if not (REPO / p).exists()]
    assert not missing, f"the workflow names paths that do not exist: {missing}"


def test_the_certification_record_does_not_claim_an_in_process_geometry_gate():
    """The `certify` job writes a provenance record, and it has been wrong once.

    It said `v007: 10.0.5 — pcbnew in-process`, while `ohm/drc.py` states the
    engine runs OUT of process always and the `v007` job installs a deliberately
    plain venv as a tripwire against in-process. It also said the geometry gate
    did "not run" at 7, 8 and 9, contradicted by a step in the same file named
    "Geometry gate — V007, at every version" that runs in all four containers.

    A record a machine wrote is only worth more than a sentence someone typed if
    the machine writes what actually ran.
    """
    text = _text()
    record = text[text.index("Write the certification record"):]
    record = record[:record.index("upload-artifact")]
    assert "in-process" not in record, "the certification record claims in-process V007"
    assert "the geometry gate at 7, 8 and 9" not in record, (
        "the record says the geometry gate did not run at 7/8/9, but the "
        "`kicad` job runs it in every container")
    # ...and it must still say what DID run.
    assert "7.0.11" in record and "10.0.5" in record
