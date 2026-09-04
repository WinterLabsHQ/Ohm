#!/usr/bin/env bash
# A `kicad-cli` that is some OTHER KiCad than the one installed.
#
# Point the roundtrip gate at it to certify a KiCad release without changing the
# machine's own toolchain:
#
#   OHM_KICAD_VERSION=10.0.5 \
#   OHM_KICAD_CLI=tests/kicad_in_docker.sh \
#     .venv/bin/python -m pytest tests/test_schematic_roundtrip.py -q
#
# POSIX HOSTS ONLY. `kicad_env.kicad_cli()` hands this path straight to
# `subprocess`, which on Windows calls CreateProcess and cannot execute a `.sh`
# — the failure is a bare `FileNotFoundError: [WinError 2]` out of subprocess,
# with nothing in it naming this file. It is also only half the story there: the
# tests that read KiCad's footprint and symbol LIBRARIES do so in-process, so
# they need the libraries on the HOST filesystem, which this shim cannot supply.
# On Windows, run the suite INSIDE the container the way
# .github/workflows/ci.yml does — docs/ci/21_compatibility_matrix.md has the
# exact `docker run`.
#
# Why this exists: "compatible with KiCad N" is a claim about a program Ohm does
# not ship and cannot inspect. The only honest way to make it is to run KiCad N
# over Ohm's output and compare — and doing that by upgrading the developer's
# KiCad trades one unverified version for another.
#
# The gate hands absolute host paths on the command line, so the host filesystem
# is mounted at the same paths inside the container and the arguments need no
# rewriting. Read-write: the gate writes its output next to its input in a
# pytest tmp_path.
set -euo pipefail

VERSION="${OHM_KICAD_VERSION:-10.0.5}"

# KiCad 7 works here. This script used to `exit 2` on any 7.x with the
# explanation that "the official images start at 8.0.4 — there is no 7.x tag and
# there never was". That was wrong, and it is written down rather than quietly
# deleted because the next person will otherwise re-derive it the same way.
#
# What is actually true (checked 2026-08-31): kicad/kicad carries 7.0, 7.0.8,
# 7.0.9, 7.0.10 and 7.0.11. `kicad/kicad:7.0.11` is linux/amd64, 440 MB, pushed
# 2024-02-23, and is built by `Dockerfile.7.0-stable` in the same
# gitlab.com/kicad/packaging/kicad-cli-docker repo as the 8, 9 and 10 images —
# cloning kicad-symbols, kicad-footprints and kicad-templates at the matching
# `7.0` branch, so the binary and the libraries agree by construction exactly as
# they do at the later versions.
#
# How the error happened: Docker Hub's tag listing is ordered by `last_updated`
# and returns 50 rows per page. 7.x was last pushed in February 2024, so it is
# not on page one, and page one is what somebody looked at. Re-check with
# `python3 tools/check_kicad_tags.py 7.0.11 8.0.9 9.0.9 10.0.5`, which paginates
# the whole list and is the tool that found this — not by eye, and not by
# `docker pull`, whose "manifest unknown" answer is indistinguishable from a
# typo.
#
# A version that genuinely has no image still fails as `docker pull`'s
# "manifest unknown" midway through the run. That is deliberately not guarded
# against here any more: a hardcoded list of which tags exist is precisely the
# thing that went stale, and the tag checker above is the honest way to ask.

IMAGE="kicad/kicad:${VERSION}"

# Mount the directories the arguments actually name — not $HOME — so a stray
# path outside them fails loudly in the container instead of silently resolving
# to something else. /tmp covers pytest's tmp_path; the repo covers examples/.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOUNTS=(-v "/tmp:/tmp" -v "${REPO}:${REPO}")
[ -n "${TMPDIR:-}" ] && [ "${TMPDIR}" != "/tmp" ] && MOUNTS+=(-v "${TMPDIR}:${TMPDIR}")

exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  "${MOUNTS[@]}" \
  -w "$(pwd)" \
  "${IMAGE}" \
  kicad-cli "$@"
