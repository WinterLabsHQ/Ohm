#!/usr/bin/env python3
"""Author-time / CI check: has a newer KiCad container image appeared than the
version this repo's certification matrix is pinned to?

Ohm's KiCad support range is a MEASUREMENT, not a claim (ADR-021): the matrix in
.github/workflows/ci.yml runs the emit, library, read-back and plot gates inside
`kicad/kicad:<tag>` at each supported major. A pin is therefore only as honest as
it is current — the moment KiCad ships a release that has an image and the matrix
does not name it, the repo is certifying a version nobody runs any more while
saying nothing about the one they do.

That gap is real today and is exactly why this exists: KiCad 10.0.6 was released
in August 2026, and `kicad/kicad:10.0.6` does not exist (HTTP 404). The stable
release and the newest containerised release are DIFFERENT NUMBERS, and only the
second one can be a gate. Nothing in the repo noticed that, and nothing would
have noticed when the image did appear.

Network I/O, so this lives in tools/ and never on the build path — the compiler
is hermetic (N1) and may not read the network under any circumstances. Like
tools/gen_fpdata.py this is stdlib-only, so it runs on any interpreter without a
venv.

    python3 tools/check_kicad_tags.py 8.0.9 9.0.9 10.0.5

Exit 0: every pin is the newest image for its major.
Exit 1: a newer image exists — the message names what to bump to.
Exit 2: the pinned tag does not exist at all, or the registry could not be read.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

_API = "https://hub.docker.com/v2/repositories/kicad/kicad/tags"

# Only bare `X.Y.Z` tags. The registry also carries `-full`, `-amd64` and
# `-amd64-full` variants of every release plus rolling `X.Y` and `nightly` tags;
# a rolling tag cannot be a certification pin because the bytes behind it change
# without the pin changing, which is the whole failure this file exists to stop.
_RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def fetch_tags(timeout: float = 30.0) -> list[str]:
    """Every tag name in kicad/kicad, following pagination."""
    names: list[str] = []
    url: str | None = f"{_API}?page_size=100"
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            page = json.load(fh)
        names.extend(r["name"] for r in page.get("results", ()))
        url = page.get("next")
    return names


def newest_per_major(names: list[str]) -> dict[str, tuple[int, int, int]]:
    """major -> the highest release triple carrying a plain tag."""
    best: dict[str, tuple[int, int, int]] = {}
    for name in names:
        m = _RELEASE.match(name)
        if not m:
            continue
        triple = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        major = m.group(1)
        if triple > best.get(major, (-1, -1, -1)):
            best[major] = triple
    return best


def _fmt(triple: tuple[int, int, int]) -> str:
    return ".".join(str(n) for n in triple)


def main(argv: list[str]) -> int:
    pins = argv[1:]
    if not pins:
        print(__doc__, file=sys.stderr)
        return 2
    for pin in pins:
        if not _RELEASE.match(pin):
            print(f"error: '{pin}' is not an X.Y.Z release tag", file=sys.stderr)
            return 2

    try:
        names = fetch_tags()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"error: could not read the kicad/kicad tag list: {exc}", file=sys.stderr)
        return 2

    available = set(names)
    newest = newest_per_major(names)
    drifted = False

    for pin in sorted(pins, key=lambda p: tuple(int(n) for n in p.split("."))):
        major = pin.split(".", 1)[0]
        if pin not in available:
            print(f"MISSING  {pin} — kicad/kicad:{pin} does not exist. The matrix "
                  f"names an image nobody can pull; the job would not have run.")
            drifted = True
            continue
        top = newest.get(major)
        if top is None:                       # unreachable while `pin in available`
            print(f"MISSING  major {major} has no release tags at all")
            drifted = True
        elif _fmt(top) != pin:
            print(f"DRIFTED  {pin} -> {_fmt(top)} — kicad/kicad:{_fmt(top)} now exists. "
                  f"Bump the matrix, re-run the certification and regenerate the pad "
                  f"table; a KiCad release is exactly when a library rename lands.")
            drifted = True
        else:
            print(f"CURRENT  {pin} — newest image for major {major}")

    unpinned = sorted(set(newest) - {p.split(".", 1)[0] for p in pins}, key=int)
    if unpinned:
        print(f"note: majors with images but no pin: {', '.join(unpinned)}. "
              f"Below the supported floor, or a new major that needs a decision.")

    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
