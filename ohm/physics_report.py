"""`ohm signoff --physics-report FILE` — the physics return channel, read (V009).

Ohm records obligations and never evaluates them (`holes.py`, D2/N2): an
`obligation ripple(vout.v) < 50mV` leaves the compiler as a `HoleEntry` in
`<stem>.constraints.json` and is discharged by a solver layer downstream. Until
this module, nothing came BACK. A routed board could sign off clean while every
obligation its source declared had never been looked at, which is the one
outcome the obligation statement exists to prevent.

The solver layer writes `physics_report.json` — one verdict per `HoleEntry` —
and `ohm signoff` reads it against the source and the board it is signing off.
Every way the report fails to discharge the source is a V009:

  error    the report could not be read, or is not this schema;
  error    the report is STALE — written against a different board, a different
           source or a different Ohm. It is REFUSED, not warned about: a verdict
           about another board says nothing about this one, so once a binding
           disagrees no verdict in the file is read at all;
  error    an obligation has NO verdict (a dropped obligation). A missing verdict
           is a violation, never a pass;
  error    an obligation has more than one verdict, a verdict names an obligation
           the source does not declare, or a verdict is filed under one
           obligation's identity with another obligation's fields;
  error    an obligation is violated;
  error    a verdict mixes an estimated and a verified number in one entry, or
           claims a result with no number at all;
  warning  an obligation is unproven — the solver layer could not decide it (no
           engine for the kind, an input outside its model's envelope, nothing
           routed). Never silent, and never a pass;
  info     an obligation is waived, or proven by an ESTIMATE rather than a
           verified measurement; and one summary line counting both labels.

**Estimated and verified are two fields, never one.** A verdict's evidence
carries its number under `verified` or under `estimated`, and exactly one of the
two when the verdict claims a result. A label written beside a number can drift
from it; a number filed under its label cannot.

Identity. `HoleEntry` has no id, so a verdict names its obligation by `hole_ref`,
derived here from `(kind, args, op)` and the hole's ordinal WITHIN its
`(kind, args, op)` group — never a global ordinal (inserting an unrelated
obligation would rename every hole after it) and never `bound` (tightening a
budget must make a verdict stale, which the `constraints_hash` binding already
does, not orphan it). Separators are ASCII US/RS, and an empty argument is GS, so
the encoding is injective over every args tuple.

Pure: `(IRLow, report bytes, digests) -> diagnostics`. No I/O (N1): the CLI reads
the three files and hands over their bytes. Nothing here runs when the flag is
absent, so a signoff without `--physics-report` is byte-for-byte what it was.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from ohm import __version__, ir
from ohm.diagnostics import Diagnostic, Span
from ohm.emit_constraints import emit_constraints

SCHEMA = "ohm.physics_report"  # the reader owns the id; any solver layer may write it
VERSION = "1"
VERDICTS = ("proven", "violated", "unproven", "waived")
# The keys a report binds itself with. `ohm_commit` is checked for shape only:
# a running Ohm cannot know its own commit without shelling out to git, which
# the build path may not do (N1). The producer checks it against its pin.
BINDINGS = ("ohm_commit", "ohm_version", "ohm_source_sha256", "constraints_hash",
            "kicad_pcb_hash")

_SPAN = Span(1, 1, 1, 1)
_FIELD_SEP = "\x1f"  # US — between kind / args / op / ordinal
_ARG_SEP = "\x1e"  # RS — between args
_EMPTY_ARG = "\x1d"  # GS — an arg that is the empty string
_HEX = frozenset("0123456789abcdef")


def hole_ref(kind: str, args: tuple[str, ...], op: str, ordinal: int) -> str:
    """`"h:" + sha256(kind US args US op US ordinal)[:32]` — see the module docstring."""
    encoded = _ARG_SEP.join(a if a else _EMPTY_ARG for a in args)
    payload = _FIELD_SEP.join([kind, encoded, op, str(ordinal)])
    return "h:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def identify(holes: tuple[ir.Hole, ...]) -> list[tuple[str, str, tuple[str, ...], str, str]]:
    """`(hole_ref, kind, dotted args, op, bound)` per hole, in the compiler's walk order."""
    seen: Counter[tuple[str, tuple[str, ...], str]] = Counter()
    out = []
    for h in holes:
        args = tuple(".".join(a) for a in h.args)  # exactly what emit_constraints writes
        group = (h.kind, args, h.op)
        out.append((hole_ref(h.kind, args, h.op, seen[group]), h.kind, args, h.op, h.bound_repr))
        seen[group] += 1
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= _HEX


def _describe(kind: str, args: tuple[str, ...], op: str, bound: str) -> str:
    return f"{kind}({', '.join(args)}) {op} {bound}"


def check_physics_report(low: ir.IRLow, report: bytes, report_file: str,
                         source: bytes, board: bytes) -> list[Diagnostic]:
    """V009 over one report, for the source and board being signed off."""
    diags: list[Diagnostic] = []

    def d(sev: str, msg: str, help_: str | None = None) -> None:
        diags.append(Diagnostic(sev, "V009", msg, _SPAN, report_file, help_))

    holes = identify(low.holes)
    unread_help = ("re-run the solver layer that writes physics_report.json against this "
                   "source and this board; every obligation counts as having no verdict "
                   "until a readable report is given")

    # ---- the report as a document ----
    try:
        doc = json.loads(report.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        d("error", f"the physics report could not be read: it is not UTF-8 JSON ({exc}); "
          f"{len(holes)} obligation(s) have no verdict", unread_help)
        return diags
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("version") != VERSION:
        found = (f"schema {doc.get('schema')!r} version {doc.get('version')!r}"
                 if isinstance(doc, dict) else f"a JSON {type(doc).__name__}")
        d("error", f"the physics report could not be read: expected schema {SCHEMA!r} "
          f"version {VERSION!r}, found {found}; {len(holes)} obligation(s) have no verdict",
          unread_help)
        return diags
    shape = [k for k in BINDINGS if not isinstance(doc.get(k), str) or not doc.get(k)]
    if not isinstance(doc.get("verdicts"), list):
        shape.append("verdicts")
    vocab = doc.get("unsupported_vocabulary")
    if not isinstance(vocab, list) or not all(isinstance(k, str) for k in vocab):
        shape.append("unsupported_vocabulary")
    if not isinstance(doc.get("declared_obligations"), int) or isinstance(
            doc.get("declared_obligations"), bool):
        shape.append("declared_obligations")
    if shape:
        d("error", "the physics report could not be read: missing or ill-typed "
          f"field(s) {', '.join(shape)}; {len(holes)} obligation(s) have no verdict",
          unread_help)
        return diags

    # ---- freshness: refused, never warned about ----
    stale_help = ("a verdict is a statement about ONE board, ONE source and ONE compiler. "
                  "Re-run the solver layer against what is being signed off now; no verdict "
                  "in a stale report is read")
    current = {
        "ohm_version": f"ohm {__version__}",
        "ohm_source_sha256": _sha256(source),
        "constraints_hash": _sha256(emit_constraints(low).encode("utf-8")),
        "kicad_pcb_hash": _sha256(board),
    }
    stale = [(k, doc[k], v) for k, v in current.items() if doc[k] != v]
    if not _is_hex(doc["ohm_commit"], 40):
        stale.append(("ohm_commit", doc["ohm_commit"], "a 40-hex commit"))
    for key, recorded, now in stale:
        d("error", f"the physics report is stale and was refused: its {key} is "
          f"{recorded!r}, and what is being signed off has {now!r}", stale_help)
    if stale:
        d("error", f"{len(holes)} obligation(s) have no verdict: the only report given was "
          "refused as stale", stale_help)
        return diags

    if doc["declared_obligations"] != len(holes):
        d("error", f"the physics report declares {doc['declared_obligations']} obligation(s) "
          f"and the source declares {len(holes)} — the report's count does not reconcile",
          "a report whose count disagrees with the source dropped or invented obligations")
    elif len(doc["verdicts"]) != doc["declared_obligations"]:
        d("error", f"the physics report declares {doc['declared_obligations']} obligation(s) "
          f"and carries {len(doc['verdicts'])} verdict(s) — the report's count does not "
          "reconcile", "exactly one verdict per obligation: a count and a list that disagree "
          "mean one of them is wrong")

    # ---- one verdict per obligation ----
    by_ref = {ref: (kind, args, op, bound) for ref, kind, args, op, bound in holes}
    unsupported = set(vocab or ())
    filed: dict[str, list[dict]] = {}
    for i, entry in enumerate(doc["verdicts"]):
        problem = _malformed(entry)
        if problem:
            d("error", f"verdict #{i} is malformed: {problem}",
              "each verdict names hole_ref, kind, args, op, bound, verdict and evidence")
            continue
        ref = entry["hole_ref"]
        fields = (entry["kind"], tuple(entry["args"]), entry["op"], entry["bound"])
        if ref not in by_ref:
            d("error", f"verdict #{i} is for an obligation the source does not declare: "
              f"{_describe(*fields)} ({ref})",
              "a verdict for an obligation that is not in the source is a verdict about "
              "some other design")
            continue
        if fields != by_ref[ref]:
            d("error", f"verdict #{i} is filed under {ref}, which is "
              f"'{_describe(*by_ref[ref])}', but names '{_describe(*fields)}'",
              "the identity and the fields disagree, so the verdict cannot be trusted for "
              "either obligation")
            continue
        verdict, evidence = entry["verdict"], entry["evidence"]
        verified, estimated = evidence.get("verified"), evidence.get("estimated")
        if verified and estimated:
            d("error", f"verdict #{i} ({_describe(*fields)}) carries both a verified and an "
              "estimated number; estimated and verified are never mixed in one verdict",
              "file the number under the one label it has earned")
            continue
        if verdict in ("proven", "violated") and not (verified or estimated):
            d("error", f"verdict #{i} ({_describe(*fields)}) is '{verdict}' with no "
              "measurement under either 'verified' or 'estimated'",
              "a result without a number is an assertion, not a measurement")
            continue
        if verdict in ("proven", "violated") and fields[0] in unsupported:
            d("error", f"verdict #{i} ({_describe(*fields)}) is '{verdict}', but the report "
              f"lists '{fields[0]}' as vocabulary it has no engine for",
              "an obligation of an unsupported kind can only be unproven or waived")
            continue
        filed.setdefault(ref, []).append(entry)

    counts: Counter[str] = Counter()
    for ref, kind, args, op, bound in holes:
        what = _describe(kind, args, op, bound)
        got = filed.get(ref, [])
        if not got:
            counts["missing"] += 1
            d("error", f"obligation '{what}' has no verdict in the physics report ({ref})",
              "a dropped obligation is a violation, never a pass: the solver layer must "
              "return exactly one verdict per obligation, 'unproven' when it cannot decide")
            continue
        if len(got) > 1:
            counts["missing"] += 1
            d("error", f"obligation '{what}' has {len(got)} verdicts in the physics report "
              f"({ref}); exactly one is required",
              "two verdicts for one obligation cannot both be read, so neither is")
            continue
        entry = got[0]
        verdict, evidence = entry["verdict"], entry["evidence"]
        label = "verified" if evidence.get("verified") else "estimated"
        number = evidence.get("verified") or evidence.get("estimated")
        reason = evidence.get("reason") or "no reason given"
        counts[verdict] += 1
        if verdict == "proven":
            counts[label] += 1
            if label == "estimated":
                d("info", f"obligation '{what}' is proven by an ESTIMATE, not a verified "
                  f"measurement: {number}")
        elif verdict == "violated":
            d("error", f"obligation '{what}' is violated ({label}): {number}",
              "fix the board (or the obligation) and re-run the solver layer")
        elif verdict == "unproven":
            d("warning", f"obligation '{what}' is unproven: {reason}",
              "unproven is not a pass: the solver layer could not decide it")
        else:
            d("info", f"obligation '{what}' is waived: {reason}")
    d("info", f"physics report: {len(holes)} obligation(s); {counts['proven']} proven "
      f"({counts['verified']} verified, {counts['estimated']} estimated), "
      f"{counts['violated']} violated, {counts['unproven']} unproven, "
      f"{counts['waived']} waived, {counts['missing']} without a verdict")
    return diags


def _malformed(entry: object) -> str | None:
    """What is wrong with one verdict entry's SHAPE, or None."""
    if not isinstance(entry, dict):
        return f"a JSON {type(entry).__name__}, not an object"
    for key in ("hole_ref", "kind", "op", "bound", "verdict"):
        if not isinstance(entry.get(key), str):
            return f"'{key}' is not a string"
    if not isinstance(entry.get("args"), list) or not all(
            isinstance(a, str) for a in entry["args"]):
        return "'args' is not a list of strings"
    if entry["verdict"] not in VERDICTS:
        return f"verdict {entry['verdict']!r} is not one of {', '.join(VERDICTS)}"
    evidence = entry.get("evidence")
    if not isinstance(evidence, dict):
        return "'evidence' is not an object"
    for key in ("verified", "estimated", "reason"):
        if evidence.get(key) is not None and not isinstance(evidence[key], str):
            return f"evidence '{key}' is neither a string nor null"
    if entry["verdict"] in ("unproven", "waived") and not evidence.get("reason"):
        return f"a '{entry['verdict']}' verdict carries no reason"
    return None
