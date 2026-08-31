"""One registry for every external-format version stamp Ohm writes.

Ohm emits eight external file formats, and every one of them carries a version
stamp of some kind — a numbered file-format revision, a schema string, a header
line an authoring tool itself writes. Before this module those stamps were bare
literals scattered across nine emitter files: some named locally, most not, none
of them explaining *why that value* to a reader who did not also read the format's
spec. A literal nobody named is a literal anybody can change in one place with
nothing noticing.

THE POLICY, stated once instead of nine times: write the oldest stamp a current
reader accepts; bump only when a reader proves the new one is needed. A newer
stamp never buys correctness — every reader in this table reads forward — and it
always costs something: an older installation's ability to open the file, or (for
the CFB/Altium constants) agreement with what the authoring tool itself writes,
which is the only ground truth a reverse-engineered binary format has. ADR-021
states this for the four KiCad constants specifically, with the KiCad-9/KiCad-10
breakage history that motivated it; ADR-022 (a later loop) generalises the policy
this module already encodes for the other four formats. Nothing here changes a
value — this is the registry those nine files now point at, not a new decision.

Hermetic (N1): stdlib only, plus `shared` for the one stamp that belongs to the
open/proprietary seam rather than to Ohm. No subprocess, no network, no clock,
no file I/O — `tests/test_formats.py` AST-walks this file to enforce it, the same
way `tests/test_lower_emit.py` enforces it for the rest of the build path.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.constraints_schema import SCHEMA_VERSION as _CONSTRAINTS_SCHEMA_VERSION


@dataclass(frozen=True)
class FormatVersion:
    """One version stamp: the bytes/string Ohm writes, and why that value."""

    artifact: str  # file extension or format name a human recognises
    constant: str  # the name the value is exported under, in `owner`
    value: int | str  # the exact bytes/string written — never changed by this module
    owner: str  # dotted module path of the emitter that writes it
    reason: str  # one line: why this value and not a newer or older one


# ---------------------------------------------------------------- KiCad (ADR-021 sec 1)
# KiCad reads FORWARD, and that is the direction it guarantees: a newer stamp would
# buy nothing and cost every older KiCad the ability to open the file. See the policy
# comment at ohm/emit_kicad.py:49-57 and ADR-021 section 1, which names these four
# constants explicitly and records the KiCad-9/KiCad-10 breakage that made "structural,
# not layout-anchored readers" the corollary this module doesn't need to restate.
_SCH_VERSION = 20230121  # KiCad 7 schematic format
_PCB_VERSION = 20221018  # KiCad 7 board format
_FP_VERSION = 20221018  # KiCad 7 board/footprint format (same format as .kicad_pcb)
_SYM_VERSION = 20220914  # KiCad 7 symbol format

# ---------------------------------------------------------------- Eagle
# The schema revision that introduced XML libraries — the oldest Eagle a `.lbr`
# with this header can be a valid file in, and the only spelling any Eagle
# understands (there is no XML library format older than 6.3 to fall back to).
_EAGLE_VERSION = "6.3"

# ---------------------------------------------------------------- Altium .PcbLib
# Neither of these is an Altium Designer release number. They are strings Altium
# Designer itself writes into the binary parameter block of a library it authored,
# read verbatim off a real Altium-authored .PcbLib the way every other unknown
# field in ohm/pkggen/altium.py was: "an unknown field set to zero is a guess, an
# unknown field set to what Altium writes is a fact." There is no newer or older
# value to choose between — this is the one value a reader checks for.
_PCB_HEADER = "PCB 6.0 Binary Library File"
_PCB_LIB_VERSION = "3.00"  # VERSION= in the library-parameters block

# ---------------------------------------------------------------- Altium .SchLib
# Same rule, same source: read off a real Altium-authored .SchLib, not chosen.
_SCH_HEADER = ("Protel for Windows - Schematic Library Editor Binary File "
               "Version 5.0")
_SCH_LIB_MINOR_VERSION = 2  # MinorVersion= in the .SchLib header record

# ---------------------------------------------------------------- MS-CFB container
# The container both Altium files live in (ohm/pkggen/cfb.py). Version 3 uses
# 512-byte sectors and is what every Altium-authored file in the wild uses; version
# 4 (4096-byte sectors, a directory-sector count in the header) exists in the spec
# but nothing Ohm has read off real Altium output writes it. Like the two Altium
# strings above, this is a fact about what the authoring tool produces, not a
# format-negotiation choice Ohm gets to make.
_CFB_VERSION = 3

# ---------------------------------------------------------------- Allegro
# The exact quoted string a real OrCAD-written pstxprt.dat carries in its
# PST_VERSION directive (ohm/emit_allegro.py:1-21: "Syntax ... taken from a real
# OrCAD-written triplet, not from prose"). Allegro's logic importer matches this
# string, so it is copied rather than invented.
_PST_VERSION = "PST_HDL_CENTRIC_VERSION_0"

# ---------------------------------------------------------------- STEP
# ISO 10303-214 (AP214, "Automotive Design") — the schema every STEP reader Ohm
# has tested against (gmsh/OpenCASCADE) accepts for a bare FACETED_BREP solid.
# AP242 supersedes AP214 in newer STEP tooling; a later loop evaluates bumping to
# it under the same forward-compat argument ADR-021 makes for KiCad. Not this one:
# this loop is registry-only and changes no value.
_HEADER_SCHEMA = "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }"

# ---------------------------------------------------------------- Ohm's own netlist
# The export-format version letter in Ohm's KiCad-style s-expression netlist
# (manual P4). "E" is the version KiCad's own netlist exporter currently writes;
# Ohm's netlist is a sibling format a BOM/CAM tool reads the same way, so it
# carries the same version token rather than inventing a competing one.
_NETLIST_VERSION = "E"

# ---------------------------------------------------------------- constraints seam
# shared/constraints_schema.py is the single seam between the open compiler and the
# proprietary platform (shared/constraints_schema.py:1-5). shared/ must not import
# from ohm/ — that would make the seam depend on the compiler it is meant to be
# independent of — so the dependency here runs the other way: this registry imports
# SCHEMA_VERSION FROM shared, it does not define it. `owner` names shared's module,
# not ohm's, to keep that direction visible to anything that reads this table.
_SCHEMA_VERSION = _CONSTRAINTS_SCHEMA_VERSION


REGISTRY: tuple[FormatVersion, ...] = (
    FormatVersion(
        artifact=".kicad_sch", constant="_SCH_VERSION", value=_SCH_VERSION,
        owner="ohm.emit_kicad",
        reason="KiCad reads forward; KiCad 7 is the oldest format every "
               "supported KiCad (7-10) still opens, and never bumped (ADR-021)."),
    FormatVersion(
        artifact=".kicad_pcb", constant="_PCB_VERSION", value=_PCB_VERSION,
        owner="ohm.emit_kicad",
        reason="KiCad reads forward; KiCad 7 is the oldest format every "
               "supported KiCad (7-10) still opens, and never bumped (ADR-021)."),
    FormatVersion(
        artifact=".kicad_mod", constant="_FP_VERSION", value=_FP_VERSION,
        owner="ohm.pkggen.footprint",
        reason="KiCad reads forward; KiCad 7 is the oldest format every "
               "supported KiCad (7-10) still opens, and never bumped (ADR-021)."),
    FormatVersion(
        artifact=".kicad_sym", constant="_SYM_VERSION", value=_SYM_VERSION,
        owner="ohm.pkggen.symbol",
        reason="KiCad reads forward; KiCad 7 is the oldest format every "
               "supported KiCad (7-10) still opens, and never bumped (ADR-021)."),
    FormatVersion(
        artifact=".lbr", constant="_EAGLE_VERSION", value=_EAGLE_VERSION,
        owner="ohm.pkggen.eagle",
        reason="the schema revision that introduced XML libraries — the oldest "
               "Eagle library format an XML .lbr with this header can be."),
    FormatVersion(
        artifact=".PcbLib", constant="_PCB_HEADER", value=_PCB_HEADER,
        owner="ohm.pkggen.altium",
        reason="the file-kind header Altium Designer itself writes into a "
               "PCB library it authored; read off a real one, not chosen."),
    FormatVersion(
        artifact=".PcbLib", constant="_PCB_LIB_VERSION", value=_PCB_LIB_VERSION,
        owner="ohm.pkggen.altium",
        reason="the VERSION= Altium Designer itself writes in the "
               "library-parameters block of a PCB library it authored."),
    FormatVersion(
        artifact=".SchLib", constant="_SCH_HEADER", value=_SCH_HEADER,
        owner="ohm.pkggen.altium",
        reason="the file-kind header Altium Designer itself writes into a "
               "schematic library it authored; read off a real one, not chosen."),
    FormatVersion(
        artifact=".SchLib", constant="_SCH_LIB_MINOR_VERSION",
        value=_SCH_LIB_MINOR_VERSION, owner="ohm.pkggen.altium",
        reason="the MinorVersion= Altium Designer itself writes in the "
               "header record of a schematic library it authored."),
    FormatVersion(
        artifact="MS-CFB container", constant="_CFB_VERSION", value=_CFB_VERSION,
        owner="ohm.pkggen.cfb",
        reason="512-byte sectors, which is what every Altium-authored file in "
               "the wild uses; version 4 (4096-byte sectors) is unobserved."),
    FormatVersion(
        artifact="Allegro pstxprt.dat", constant="_PST_VERSION",
        value=_PST_VERSION, owner="ohm.emit_allegro",
        reason="the exact PST_VERSION string a real OrCAD-written triplet "
               "carries; taken from that triplet's syntax, not from prose."),
    FormatVersion(
        artifact=".step", constant="_HEADER_SCHEMA", value=_HEADER_SCHEMA,
        owner="ohm.pkggen.step",
        reason="ISO 10303-214 (AP214), which every STEP reader Ohm has tested "
               "against accepts; AP242 supersedes it and a later loop evaluates "
               "the bump — this loop changes no value."),
    FormatVersion(
        artifact="Ohm netlist", constant="_NETLIST_VERSION",
        value=_NETLIST_VERSION, owner="ohm.emit_netlist",
        reason="the export-format version letter KiCad's own netlist exporter "
               "currently writes; Ohm's netlist is a sibling format, not a "
               "competing one, so it carries the same token."),
    FormatVersion(
        artifact="constraints.json", constant="SCHEMA_VERSION",
        value=_SCHEMA_VERSION, owner="shared.constraints_schema",
        reason="the open<->proprietary seam's own schema version; owned by "
               "shared/, imported here rather than defined here, because "
               "shared/ must not depend on ohm/ (see module docstring)."),
)
