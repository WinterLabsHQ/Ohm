"""S011: two parts cannot occupy the same space.

`ohm/emit_kicad.py` claimed "No courtyard overlaps by construction (disjoint
cells)". That was true of AUTO-placed parts and only of those: a part with a
`constraint fixed(x, y)` sits exactly where the source says, and nothing checked
whether the source said something impossible. On the fmuv2 reference board it
had: a connector, a mounting hole and a fiducial declared on top of each other,
five overlapping pairs, which KiCad's DRC found and Ohm did not.

The check runs over the SAME `placement()` the emitter uses, so a board Ohm
emits and the verdict Ohm gives cannot disagree — checking the source's declared
poses separately would let the two drift.

Severity follows what Ohm actually KNOWS. A `generate(...)` package has real
computed geometry, so an overlap is a fact: error. A string-footprint part has
none — Ohm places it on a 5x5mm placeholder — so an overlap is a conflict of the
placeholder, not a proven collision: warning, saying so. The five overlaps KiCad
DRC found on fmuv2 are all of this second kind, because the board embeds that
same placeholder courtyard for stub footprints; calling them errors would be
Ohm asserting a fact about a footprint it has never read.

Limits, stated rather than hidden: courtyards are compared as axis-aligned
rectangles. A part rotated by 90/270 has its courtyard transposed; any other
angle is compared on the bounding box of the rotated rectangle, which is
conservative (it can only over-report, never miss a real overlap).
"""

from __future__ import annotations

from decimal import Decimal

from ohm import ir
from ohm.diagnostics import Diagnostic, Span
# The placement model, from the module that OWNS it. This check exists to say
# whether the board Ohm will write honours what the source declared, so it has
# to read the very placement the emitter writes rather than form a second
# opinion about it — see the S011 note above. That dependency used to be hidden
# inside the function bodies below, because `_extent` lived here and the emitter
# imported it back, making the two modules mutually dependent; `_extent` now
# lives with the rest of the placement geometry and the edge runs one way, so it
# is declared here where a reader can see it.
from ohm.emit_kicad import (_courtyard, _extent, fixed_poses, near_follows,
                            placement)

_D = Decimal
_QUANTUM = _D("0.01")  # the PCB coordinate quantum; a sub-quantum miss is a tie


def _near_gap(pa, pb, ka, kb) -> Decimal:
    """Edge-to-edge separation between two placed parts, in mm.

    `near` is declared edge to edge — placing on the 2mm cell grid instead turned
    a declared 2mm into 2.9mm, which is why `placement()` measures it this way
    and why S013 has to measure it the same way. Nested boxes give a negative
    result, which is fine: it is below any declared distance.
    """
    ax, ay, arot = pa[0], pa[1], pa[2]
    bx, by, brot = pb[0], pb[1], pb[2]
    ahw, ahh = _extent(ka[0], ka[1], arot)
    bhw, bhh = _extent(kb[0], kb[1], brot)
    return max(abs(ax - bx) - (ahw + bhw), abs(ay - by) - (ahh + bhh))


def _check_near(low: ir.IRLow, path: str) -> list[Diagnostic]:
    """S013: every `near` the source declared, measured against the placement.

    `near` is the one constraint whose delivery nothing checked. S011 asks
    whether two parts collide; this asks whether the adjacency the source asked
    for actually arrived, using the SAME `placement()` the emitter writes — the
    S011 rule, so a board Ohm emits and the verdict Ohm gives cannot disagree.

    Two severities, because there are two different facts:

    - **error** when the placer never even received the declaration. A second
      `near` on a follower that already has one, a `near` on a part pinned by
      `fixed`, or a `near` naming something that is not a part on this board.
      That is the SOURCE declaring an adjacency no placement can act on, and it
      is the same reasoning S011 uses for a real courtyard overlap: the source
      said something impossible. Erroring stops the build before anything is
      written, which is the only reason `ohm build` on a `near` cycle no longer
      leaves a half-written output directory behind.
    - **warning** when the placer tried and the achieved separation is wider
      than declared. That is a limit of Ohm's placer rather than a defect in the
      source, so it is reported honestly rather than blamed on the author.
      `--strict` still catches it. The message names WHICH limit, because there
      are two and they have different fixes: congestion (every side of the
      target was already taken) and a `near` CYCLE (no member is a root, so the
      placer seats what it can and flows the one that is left). Saying
      "every side was taken" about a cycle is a cause the check has not
      established — on `near(a,b) near(b,c) near(c,a)` the part that loses is
      flowed before anything else is placed at all.

    Silent on a `near` that was honoured, which includes a cycle it managed to
    satisfy anyway: `near(a, b)` plus `near(b, a)` reads as unsatisfiable and is
    not — two parts CAN each sit beside the other. Ohm places them 2mm apart and
    says nothing, because nothing is wrong. That is why this is measured rather
    than pattern-matched on the shape of the constraint graph.
    """
    follows, want_of, dropped = near_follows(low)
    if not follows and not dropped:
        return []

    diags: list[Diagnostic] = []
    span = Span(1, 1, 1, 1)
    for c in dropped:
        who = ", ".join(c.nets[:2])
        diags.append(Diagnostic(
            "error", "S013",
            f"`near({who})` cannot be honoured: the placer never received it. "
            f"Either '{c.nets[0]}' already has a `near` (the first declaration "
            f"wins), or it is pinned by `fixed` and cannot be moved, or one of "
            f"the two names is not a part on this board",
            span, file=path,
            help="a part can be declared near ONE other part; drop the "
                 "duplicate `near`, or drop the `fixed` that pins it, or fix "
                 "the misspelled reference"))

    if not follows:
        return diags

    def _in_cycle(ref: str) -> bool:
        """Whether `ref`'s `near` chain leads back to `ref`.

        A cycle has no root, so the placer cannot seat every member beside its
        target: it breaks exactly one edge and flows that part in the ordinary
        layout. Which member loses is an implementation detail, but THAT one
        loses is not — so the report has to say so, rather than blaming a
        congestion that did not happen.
        """
        seen, cur = {ref}, follows.get(ref)
        while cur is not None and cur not in seen:
            seen.add(cur)
            cur = follows.get(cur)
        return cur == ref

    poses = placement(low)
    kind_of = {p.ref: _courtyard(p.package) for p in low.parts}
    for ref, target in sorted(follows.items()):
        pa, pb = poses.get(ref), poses.get(target)
        if pa is None or pb is None:
            continue  # placement() is total over low.parts; nothing to measure
        want = want_of[ref]
        got = _near_gap(pa, pb, kind_of[ref], kind_of[target])
        if got <= want + _QUANTUM:
            continue
        if _in_cycle(ref):
            why = (f"'{ref}' is in a `near` CYCLE, and a cycle has no part to "
                   f"start from — the placer seats every member it can and "
                   f"flows the one that is left")
            fix = ("break the cycle: drop one `near`, or pin one of the parts "
                   "with `fixed(x, y)` so the rest have something to sit beside")
        else:
            why = (f"every side of '{target}' was already taken, so '{ref}' was "
                   f"flowed into the next free cell instead")
            fix = ("give the neighbours around the target more room, state a "
                   "distance the layout can hold, or place the part yourself "
                   "with `fixed(x, y)`")
        diags.append(Diagnostic(
            "warning", "S013",
            f"`near({ref}, {target})` asked for {want}mm and the placement "
            f"achieved {got}mm: {why}",
            span, file=path, help=fix))
    return diags


def check_placement(design: ir.Design, path: str = "<string>",
                    low: ir.IRLow | None = None) -> list[Diagnostic]:
    if low is None or not low.parts:
        return []
    # S013 first: `near` needs no `fixed` part anywhere, so it must not sit
    # behind the bail below. The design that crashed `ohm build` had no `fixed`
    # constraint at all.
    near = _check_near(low, path)

    fixed = fixed_poses(low)
    if not fixed:
        return near  # every part auto-placed: disjoint cells by construction

    poses = placement(low)
    boxes: list[tuple[str, Decimal, Decimal, Decimal, Decimal, str, bool]] = []
    for part in low.parts:
        pose = poses.get(part.ref)
        if pose is None:
            continue
        x, y, rot, side, _locked = pose
        w, h, model = _courtyard(part.package)
        hw, hh = _extent(w, h, rot)
        boxes.append((part.ref, x, y, hw, hh, side, model is not None))

    diags: list[Diagnostic] = list(near)
    span = Span(1, 1, 1, 1)
    for i, (ref_a, ax, ay, ahw, ahh, a_side, a_real) in enumerate(boxes):
        for ref_b, bx, by, bhw, bhh, b_side, b_real in boxes[i + 1:]:
            if a_side != b_side:
                continue  # opposite faces of the board never collide
            if ref_a not in fixed and ref_b not in fixed:
                continue  # auto cells are disjoint; do not re-litigate them
            if not (abs(ax - bx) < ahw + bhw and abs(ay - by) < ahh + bhh):
                continue
            who = " and ".join(f"'{r}'{' (fixed)' if r in fixed else ''}"
                               for r in (ref_a, ref_b))
            where = f"{ref_a} at ({ax}, {ay}), {ref_b} at ({bx}, {by})"
            if a_real and b_real:
                # both courtyards computed from real generated geometry: the
                # parts genuinely cannot both be there
                diags.append(Diagnostic(
                    "error", "S011",
                    f"courtyards overlap: {who} — parts cannot share board "
                    f"area. {where}",
                    span, file=path,
                    help="move one of the `fixed` placements, or drop the "
                         "constraint and let Ohm place the part"))
            else:
                # A string-footprint part has no geometry in the IR; Ohm places
                # it on a 5x5mm PLACEHOLDER courtyard. Calling that a collision
                # would be asserting a fact about a footprint Ohm has never
                # read — the honest report is the conflict plus the assumption.
                unknown = ", ".join(r for r, real in
                                    ((ref_a, a_real), (ref_b, b_real)) if not real)
                diags.append(Diagnostic(
                    "warning", "S011",
                    f"placement conflict: {who} overlap on Ohm's 5x5mm "
                    f"placeholder courtyard. {where}. The real land pattern of "
                    f"{unknown} is a string footprint Ohm has not read, so this "
                    "is a conflict of the PLACEHOLDER, not a proven collision",
                    span, file=path,
                    help="confirm in KiCad against the real footprint, or give "
                         "the part a generate(...) package so Ohm knows its size"))
    return diags
