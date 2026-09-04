"""One registry for every external-format version stamp Ohm writes.

Ohm emits eight external file formats on the build path, plus `.zen` on the
interop path, and every one of them carries a version stamp of some kind — a
numbered file-format revision, a schema string, a header line an authoring tool
itself writes. Before this module those stamps were bare literals scattered across
the emitter files: some named locally, most not, none of them explaining *why that
value* to a reader who did not also read the format's spec. A literal nobody named
is a literal anybody can change in one place with nothing noticing.

THE POLICY, stated once instead of once per emitter: write the oldest stamp a current
reader accepts; bump only when a reader proves the new one is needed. A newer
stamp never buys correctness — every reader in this table reads forward — and it
always costs something: an older installation's ability to open the file, or (for
the CFB/Altium constants) agreement with what the authoring tool itself writes,
which is the only ground truth a reverse-engineered binary format has. ADR-021
states this for the four KiCad constants specifically, with the KiCad-9/KiCad-10
breakage history that motivated it; ADR-022 generalises the policy to every format
in this table and tabulates, stamp by stamp, which ones a real reader has actually
been run against. Of the entries below, exactly one ever changed the bytes Ohm
writes — STEP, from AP214 to AP242 — and one is a stamp that had no version to
record before (`.zen`). Both are the entries whose readers can be executed here:
gmsh/OpenCASCADE meshes the STEP, and `pcbc` 0.4.41 builds the `.zen`. Every other
entry is the value Ohm was already writing, and the reasons say why rather than
leaving that to a reader's charity.

The count is deliberately not written out in prose here. It was — "fifteen" — and
it went stale the moment `.kicad_pro`'s three section versions and STEP's
DATA-section protocol name were registered, leaving this module, ADR-022 and
`docs/ci/12` each asserting a number none of them still had. `len(REGISTRY)` is
the number, `tests/test_formats.py` pins it, and the pin's failure message names
the documents to update with it.

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
# comment above `_SCH_VERSION`'s import in ohm/emit_kicad.py, and ADR-021 section 1,
# which names these four
# constants explicitly and records the KiCad-9/KiCad-10 breakage that made "structural,
# not layout-anchored readers" the corollary this module doesn't need to restate.
_SCH_VERSION = 20230121  # KiCad 7 schematic format
_PCB_VERSION = 20221018  # KiCad 7 board format
_FP_VERSION = 20221018  # KiCad 7 board/footprint format (same format as .kicad_pcb)
_SYM_VERSION = 20220914  # KiCad 7 symbol format

# ---------------------------------------------------------------- KiCad .kicad_pro
# The project file is JSON, and each of its sections carries its OWN schema
# version rather than the file carrying one. These are KiCad 7's, chosen for the
# same forward-compatibility reason as the four format stamps above: bump one and
# an older KiCad stops opening the project.
#
# They sat as bare literals in `ohm/emit_kicad_pro.py` and the module appeared in
# neither this registry nor its owner gate, so ADR-022's fourteen-row table never
# mentioned `.kicad_pro` at all — that ADR's amendment section now records it.
# The objection that these are "KiCad's numbers, not Ohm's choice" applies
# equally to _SCH_VERSION: what Ohm chooses is which revision to CLAIM, which is
# exactly what the policy at the top of this file governs.
_PRO_PROJECT_VERSION = 1  # the project document itself
_PRO_ERC_VERSION = 0  # the `erc` section
_PRO_NET_SETTINGS_VERSION = 3  # the `net_settings` section

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
# ISO 10303-242 (AP242, "Managed model based 3D engineering"), FIRST edition —
# the `{ 1 0 10303 442 1 1 4 }` object identifier, not edition 2's `3 1 4` or
# edition 3's `4 1 4`. This is the one stamp in the table that moved, and it moved
# because it is the one with a real reader to move it: ISO/TC 184/SC 4 stopped work
# on AP203 and AP214 when AP242 published in 2014, so "the oldest stamp a current
# reader accepts" now means the oldest AP242 edition rather than the superseded
# protocol. AP214 was never wrong — it was the first-edition AUTOMOTIVE_DESIGN
# identifier `{ 1 0 10303 214 1 1 1 1 }` and gmsh still reads it — but the policy
# asks for the oldest *current* protocol, and AP214 stopped being current.
#
# The evidence, and the reason this bump shipped when the other six did not:
# tests/test_step.py meshes the emitted file with gmsh, which embeds OpenCASCADE,
# and checks the resulting solid's volume to rel=1e-9. A schema string a reader
# rejects does not merely warn; the file yields no solid and the volume check has
# nothing to compare. Bumping AP214 -> AP242 changed nothing about that number, so
# the new stamp is proved read, not assumed read.
#
# Spelling: canonical brace spacing, matching every non-OCC writer and the AP214
# line this replaces. OCC's own literal is `{1 0 10303 442 1 1 4 }` (no space after
# the brace) — a typo in schemaAP242DIS, not a distinct identifier; readers match
# the leading name token, and gmsh reads both.
_HEADER_SCHEMA = "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }"
# The same protocol named a second time, in the DATA section's
# APPLICATION_PROTOCOL_DEFINITION. `ohm/pkggen/step.py` says outright that this
# "has to agree with FILE_SCHEMA above or the file claims one protocol in the
# header and another in the data" — and nothing enforced it, because the triple
# was three bare literals. Note the deliberate absence of the `_mim_lf` suffix:
# OpenCASCADE's `IsAP242()` lower-cases this field and compares it against
# exactly this spelling, so it is not a typo for the header name.
_STEP_PROTOCOL = "ap242_managed_model_based_3d_engineering"
_STEP_PROTOCOL_YEAR = 2013  # the AP242 DIS year OCC stamps; descriptive only
_STEP_APP_CONTEXT = "Managed model based 3d engineering"

# ---------------------------------------------------------------- Zener (.zen)
# The diodeinc/pcb toolchain lane the export targets. `pcb.toml` names one of
# "0.3", "0.4" or "nightly"; 0.4 is the current released lane (0.4.41, 2026-08-31)
# and the oldest one whose `Component()` surface Ohm's export has been run against,
# which is the whole of the reason for the value: before this loop there was no
# entry here at all, because no `pcb` binary had ever been available to this project
# and pinning a version nothing had executed would have been a decoration.
#
# ADR-002 section 5 recorded that cross-check as deferred. It is now done —
# tests/test_zen_pcb.py builds Ohm's emitted `.zen` with the real binary — and what
# it found is why `ohm/emit_zen.py` changed: the "universal Component() form" Ohm
# had been writing since P7 does not load in 0.4.x at all. So unlike every other row
# in this table, this is not a stamp inherited from a format's history; it is the
# first version of the .zen surface Ohm has ever been able to claim it writes.
#
# Written into the generated file's header comment rather than into a manifest,
# because `emit_zen` produces one `.zen` and no workspace around it: the lane belongs
# with the bytes it describes, so a file that outlives this repo still says which
# surface it was written against.
_ZEN_PCB_VERSION = "0.4"

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
        reason="ISO 10303-242 (AP242) first edition — the oldest edition of the "
               "protocol that superseded AP203/AP214 in 2014; proved read by "
               "gmsh/OpenCASCADE meshing the solid (ADR-022)."),
    FormatVersion(
        artifact=".zen", constant="_ZEN_PCB_VERSION", value=_ZEN_PCB_VERSION,
        owner="ohm.emit_zen",
        reason="the diodeinc/pcb toolchain lane the export targets and the "
               "oldest one it has been built against — 0.4.41, the first pcb "
               "binary this project has had (ADR-002 sec 5, ADR-022)."),
    FormatVersion(
        artifact=".kicad_pro", constant="_PRO_PROJECT_VERSION",
        value=_PRO_PROJECT_VERSION, owner="ohm.emit_kicad_pro",
        reason="KiCad 7's project document version; KiCad reads forward, so the "
               "oldest spelling every supported KiCad opens is the one to write."),
    FormatVersion(
        artifact=".kicad_pro erc", constant="_PRO_ERC_VERSION",
        value=_PRO_ERC_VERSION, owner="ohm.emit_kicad_pro",
        reason="KiCad 7's `erc` section version; bumping it would cost older "
               "KiCads the ERC severities Ohm sets and buy nothing."),
    FormatVersion(
        artifact=".kicad_pro net_settings", constant="_PRO_NET_SETTINGS_VERSION",
        value=_PRO_NET_SETTINGS_VERSION, owner="ohm.emit_kicad_pro",
        reason="KiCad 7's `net_settings` section version; the net classes Ohm "
               "writes are readable by every supported KiCad at this revision."),
    FormatVersion(
        artifact=".step protocol", constant="_STEP_PROTOCOL",
        value=_STEP_PROTOCOL, owner="ohm.pkggen.step",
        reason="the DATA section's half of AP242's identity, which must agree "
               "with _HEADER_SCHEMA; registered so a change to one cannot "
               "silently leave the other claiming a different protocol."),
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
