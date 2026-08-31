"""Diagnostics — a product surface (compiler design §5).

One record, two renderings: a terse machine-parseable line for agent loops and a
human block with source excerpt + caret + help. Codes are stable; severities gate
the driver, never the passes themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Span:
    line: int  # 1-based
    col: int  # 1-based
    end_line: int
    end_col: int


@dataclass(frozen=True)
class Diagnostic:
    severity: str  # "error" | "warning" | "info"
    code: str  # stable, documented in EXPLAIN below
    message: str
    span: Span
    file: str = "<string>"
    help: str | None = None
    related: tuple[Span, ...] = field(default=())
    endpoints: tuple[str, ...] = field(default=())  # net endpoints (I003/I006), rendered in json/LSP

    def machine(self) -> str:
        return f"{self.file}:{self.span.line}:{self.span.col}: {self.severity}[{self.code}]: {self.message}"

    def human(self, source: str | None = None) -> str:
        lines = [self.machine()]
        if source is not None:
            src_lines = source.splitlines()
            if 1 <= self.span.line <= len(src_lines):
                excerpt = src_lines[self.span.line - 1]
                lines.append(f"    {excerpt}")
                width = max(1, (self.end_col - self.col) if self.span.end_line == self.span.line else 1)
                lines.append("    " + " " * (self.span.col - 1) + "^" * width)
        for r in self.related:
            lines.append(f"  related: line {r.line}")
        if self.help:
            lines.append(f"    help: {self.help}")
        return "\n".join(lines)

    # convenience passthroughs used by human()
    @property
    def col(self) -> int:
        return self.span.col

    @property
    def end_col(self) -> int:
        return self.span.end_col


def has_errors(diags: list[Diagnostic]) -> bool:
    return any(d.severity == "error" for d in diags)


# `ohm explain <code>` — stable, documented cause + fix per diagnostic code
EXPLAIN: dict[str, str] = {
    "P001": "Syntax error. The parser skipped to the next top-level declaration; fix the shown line (check ':', indentation, and statement forms).",
    "R001": "Two top-level declarations share a name. Rename one; the first declaration wins for recovery.",
    "R002": "Reference to an unknown component, module, or binding. Check spelling (a suggestion is offered when close).",
    "R003": "Unknown pin/attr on a component, member on an interface, or port on a module. Check the declaration you are referencing.",
    "R004": "Two bindings in one module share a name. Rename one.",
    "R005": "io(...) needs an interface type. Declare the interface or fix the name.",
    "R006": "Every pin must declare a role (power_in, power_out, input, output, bidirectional, passive, ground, nc) — roles drive Tier-I checking.",
    "R007": "The role name is not one of the valid roles.",
    "R008": "A require relation names a member that the interface does not declare.",
    "R009": "Unknown constraint kind. Known kinds: impedance, length, diffpair, netclass, keepout.",
    "R010": "A module binding may not reuse the name of a global interface, component, or module (no shadowing in v1) — rename the binding.",
    "R011": "Connect endpoints must be a pin, a whole io() port, or an interface member — a bare component/module instance or a config value cannot carry a net.",
    "R012": "An instance override names a config/attr the target does not declare — modules accept only declared configs; components accept new attrs unless the name is a near-miss of a declared attr or pin.",
    "R013": "An argument in a require relation or obligation is not usable (keyword args and literal obligation args are ignored).",
    "R014": "A module instantiates itself, directly or through a cycle — the hierarchy cannot be built. Break the cycle.",
    "R015": "A `use` import could not be found or read. std modules use dotted form (use std.passives); local files use the quoted form (use \"./lib.ohm\").",
    "R016": "Import cycle: a file is (transitively) importing itself. Break the cycle; shared imports of the same file from two places are fine.",
    "R017": "A `kind` refinement or a component's conformance list names a kind that isn't declared. Declare the kind (or `use` the file that does).",
    "R018": "Kind refinement cycle: a kind is (transitively) its own parent. Break the cycle.",
    "R019": "A kind `param` names a physical dimension that isn't known (voltage, current, resistance, capacitance, frequency, time, length, power, temperature, dimensionless).",
    "R020": "An `expects` declaration uses an unknown pattern (pullup, decoupling, loadcaps, cap, termination, tie) or names a member the kind/interface doesn't declare.",
    "R021": "Array/loop misuse: counts and ranges must be integers 1..1024 — literal, or a config name (± literal offset) whose default is a literal dimensionless integer; array refs must be indexed within range, and a loop variable is only usable inside its own loop's index brackets.",
    "R023": "A `fn` is impure or unfoldable: duplicate/unknown name, a body ref that isn't a parameter, a call to an unknown function, wrong arity, or (transitive) recursion. fn bodies fold at compile time from params and literals only.",
    "R024": "An `if` condition must fold to a definite truth at compile time (point values, one dimension; bare expressions must be dimensionless — nonzero is true), and io()/config() must be declared unconditionally outside `if`.",
    "R022": "A computed attr/override expression must fold at compile time: literals, this module's configs with literal defaults, and closed-form arithmetic only. Folding bakes the config default in, so overriding such a config at instantiation is also R022 — pass the final value directly.",
    "Z007": "A net in the imported .zen design has only one endpoint — either an intentional boundary or a wiring mistake; Ohm cannot tell without declared intent.",
    "Z008": "A part in the imported .zen design has no connections at all.",
    "Z009": "Flat-import notice: only structural lints run on a bare .zen netlist. `ohm convert design.zen` emits annotatable Ohm source; declared intent enables the full tier stack.",
    "V001": "The routed board does not match the source: a part is missing/foreign, or a pad is bound to a different net than the source declares. Re-emit and re-route from the current source.",
    "V002": "A net's copper does not join all of its pads — the net is incomplete (islands remain). Route the net, or fix endpoint coordinates so copper actually meets.",
    "V003": "Copper the source never asked for: segments on no net (net 0), on nets the source does not define, or trace islands touching no pad of their net.",
    "V004": "Copper of two different nets meets at one point (segment endpoint, via, or pad) — a short at the topology level. Geometric-overlap shorts are kicad-cli DRC's job.",
    "V005": "Signoff scope notice (info): zones and pad-less stub footprints are not traced, and geometric rules (clearance/overlap) are delegated to `kicad-cli pcb drc`.",
    "V006": "A part placed with `constraint fixed(...)` moved or rotated on the routed board. Fixed placement is a source-level promise the router must keep.",
    "V007": "A geometry violation from KiCad's own DRC engine, run by `ohm signoff`. Geometry (clearance, courtyard overlap, silk over pads, board outline) is a property of the board rather than of the source, so the board's engine is the oracle; Ohm reports one line per rule class with a count. Unconnected pads and unresolved library references are reported as info: an Ohm board is unrouted until a router runs, and it carries its footprint geometry inline rather than by library reference.",
    "U001": "Adding/subtracting quantities of different dimensions (e.g. volts + ohms). Convert or fix the expression.",
    "U002": "Comparing quantities of different dimensions. Both sides of a comparison must share a dimension.",
    "U003": "Unknown unit. See the v1 unit table in the language spec.",
    "U004": "A tolerance's unit is incompatible with the quantity's unit (e.g. 10kohm +/- 5V).",
    "S001": "Two pins map to the same pad. Each pad takes exactly one pin.",
    "S002": "A pin maps to a pad outside the generated package's pad set (pins=N produces pads 1..N).",
    "S003": "The pin↔pad loop on a generated package is incomplete: a pin has no pad, or a generated pad has no pin.",
    "S004": "A pin of a component with an external footprint has no pad= mapping — unpadded pins emit pin names into the netlist and import unconnected in KiCad. Map every pin.",
    "S005": "A component must declare a package: a footprint id string or generate(...).",
    "S006": "The generate(...) spec cannot be built: unknown package type (see the v1 catalog), bad or non-integer pin count, or an unrecognized argument (CHIP families accept only density=).",
    "Q006": "A Tier-Q check PASSED, but the rating it compared against is a bare literal on a part whose limit is programmed by an external component (a current-limit resistor, a feedback divider). Ohm cannot fold that resistor into the rating, so the pass is only as good as the number someone typed. Verify it against the part's programming equation by hand — or assert it: `fn ilim(r) = 6800ohm*A / r` then `assert ilim(r_ilim.value) >= <budget>`.",
    "B011": "A specific, orderable part (declares `mpn=`) leaves pins unconnected without saying so. A pin that floats by design and a pin nobody considered are indistinguishable to every other check. Write `must_connect=false` where floating is correct, or `must_connect=ground` / `must_connect=rail` for reserved pins whose datasheet specifies a tie — that turns an omission into a decision.",
    "R025": "`must_connect` accepts true, false, ground or rail. `ground`/`rail` require the pin to land on a net of that class, which is what a datasheet means by \"RES — connect to GND\"; wiring such a pin to a signal is as wrong as floating it.",
    "R026": "A design is one document, so it has at most one `board` block. Put per-variant identity in that block's fields (rev, title) rather than in a second block.",
    "R027": "Unknown field in a `board` block. The set is closed on purpose: an unrecognised key is a typo that would disappear silently into a title block nobody can cite. Known fields: title, rev, date, company, designer, license, comment.",
    "M009": "The design has no `board` block, or its block has no title/rev, so the emitted schematic's title block is blank. A drawing with no title, revision or owner is a draft artifact: it cannot be cited in an ECO, a design review or a fab package. Advisory like the rest of the M tier — it never blocks a build.",
    "R028": "Two `net` declarations name the same net. The labels they point at are already connected, so they are one signal and cannot have two names. Keep one declaration.",
    "M010": "One or more nets are named after a component pin (`FMU_C_Y1_P1`, from `fmu.c_y1.P1`) because nothing else named them: Ohm elects the shallowest label on a net, and when no port label is present a part pin wins. Such a name tells a reviewer nothing about the signal and becomes wrong the moment the part is renumbered. Declare the signal instead: `net OSC24_IN = y1.P1`.",
    "M011": "A capacitor sits across a supply rail with no `vmax` declared. The value alone does not specify a capacitor: the voltage rating decides whether it survives the rail, and the dielectric decides how much capacitance survives the bias (X7R vs X5R is a real difference on a 5V input). Only rail-facing caps are checked, so the warning stays load-bearing. Declare it at the use site: Capacitor(value=100nF, vmax=16V, dielectric=\"X7R\").",
    "Q007": "A `netclass` names a rail's routing rule, but nothing on that net declares a `current=`, so Ohm has no number to size its track width from and the class keeps KiCad's default 0.25mm. A class called \"power\" routed at signal width is a label, not a rule. Declare the draw on the pins that take it (`pin VDD: role=power_in, current=250mA`) and the width follows from IPC-2221A.",
    "D001": "Two pieces of text on the schematic overlap. A net LABEL cannot be moved to fix this — it is bound to the wire it names, and sliding it off would disconnect the net — so the fix is either more room on that side of the part, or moving the text that is free (a property, a frame title).",
    "D002": "Text prints over a symbol body, hiding its pin names. The space a name needs has to be reserved when the part's cell is sized, not found afterwards: a 14-character rail name is 13mm of drawing.",
    "D003": "Two symbols are drawn on top of each other — the placement reserved less space than they occupy. Usually a part positioned relative to another (a passive riding an IC pin, a series chain) whose full extent was not counted.",
    "D004": "Two elements share a uuid. KiCad's behaviour on duplicates is undefined. Derive each id from something unique to the element — its coordinates as well as its net, for instance — rather than from a name that can repeat.",
    "S012": "A component with an external footprint (`package = \"Lib:Footprint\"`) names a pad that footprint does not carry. Ohm emits a pad-less stub and KiCad supplies the geometry from the library installed on that machine, so the pad id is the only thing that travels — name one that is not there and the pin arrives in KiCad unconnected, with nothing anywhere saying so. KiCad 10 renamed shield and shell pads to \"SH\" across 163 footprints, so `pin SHIELD: pad=11` was right for years and is wrong on upgrade. Checked against a table of KiCad's own footprints shipped with Ohm (majors 8, 9 and 10), never against your disk — compilation is hermetic. ERROR when `--target-kicad` names a version the table covers, because then the answer is definite; WARNING otherwise, naming each covered version that lacks the pad and what it calls it instead, because you have not said which KiCad you build for. Silent on any footprint the table does not know: your own and third-party libraries are unknown, not wrong.",
    "S011": "Two parts overlap on the board, and at least one of them is placed by `constraint fixed(...)` — so the source says where it goes, and the source says something impossible. ERROR when both courtyards come from real `generate(...)` geometry; WARNING when either part is a string footprint, because Ohm has never read that land pattern and places it on a 5x5mm placeholder, making the conflict one of the placeholder rather than a proven collision. Courtyards are compared axis-aligned; a part turned by an angle that is not a multiple of 90 degrees is bounded by its diagonal, which can over-report but never miss a real overlap.",
    "S010": "Ohm's LGA/BGA families are GRID arrays with JEDEC alphanumeric pads; `border=` keeps the outer rings. Datasheets also say \"LGA\" for leadless PERIMETER packages whose pads are numbered 1..N around the outline — those are QFN/DFN here. A border ring with unequal sides (e.g. 5x5 border=1 gives 5/3/3/5) almost never matches such a part, and because the pin<->pad map stays total, S003 cannot see the mismatch.",
    "S007": "`pinout_source` is not readable. Use \"kicad:<Lib>:<Symbol>\" for a map taken from a KiCad symbol library (machine-checkable by `ohm verify-pinouts`) or \"datasheet:<reference>\" for a human attestation naming the document and table.",
    "S008": "A component's pin↔pad map has changed since it was verified against its `pinout_source`: the recorded `pinout_digest` no longer matches the declared pins. A pad was moved, added or removed. Re-verify against the source before updating the digest — this is the check that stands between a transposed pin and copper. Roles, ratings and must_connect are NOT covered by the digest and may change freely.",
    "S009": "A specific, orderable part (declares `mpn=`) carries a multi-pad pin map with no recorded origin, or names an origin without freezing a digest. S003 proves a pin↔pad map is TOTAL; it can never prove it is CORRECT. Run `ohm import-symbol` to take the map from a real KiCad library (it stamps both attrs), or attest it by hand with pinout_source = \"datasheet:<ref>\".",
    "I001": "Connecting two different interface types. Connect same-type interfaces, or wire members explicitly to adapt.",
    "I002": "Connecting a whole interface to a single pin/member. Connect member-to-member or interface-to-interface.",
    "I003": "Multiple drivers (output/power_out) on one net. Exactly one driver may drive a net.",
    "I005": "Swapped members of a require-related interface (e.g. SDA↔SCL, D+↔D−). Connect same-named members.",
    "I006": "A net has input endpoints and no driver (even through passives, an input behind a series resistor still floats). Connect a driver.",
    "I007": "A constraint references a pin or port that is not connected to any net. Connect the endpoint, or drop it from the constraint.",
    "C001": "A closed-form assert evaluated to false. The message shows both sides' computed values.",
    "C002": "A closed-form assert may fail over declared tolerances (worst-case interval overlaps the bound).",
    "C003": "The build cannot evaluate this assert: a referenced value is not declared. Declare a config default, component attr, or rating.",
    "C004": "An assert must be a comparison (<, <=, >, >=, ==, !=); a bare value or call has no truth value the build can check.",
    "Q001": "A net's declared voltage exceeds a pin's vmax rating.",
    "Q002": "Declared current draws on a net exceed the driver pin's imax rating.",
    "Q003": "Worst-case (ΔV)²/R dissipation in a 2-pin resistive part exceeds pmax.",
    "Q004": "Conflicting declared voltages on one net (two different rails, or a driver shorted to ground).",
    "Q005": "Coarse over-temperature: 25 degC ambient + worst-case dissipation × theta_ja exceeds the part's declared tmax. Declare theta_ja (degC/W) and tmax on the part (ADR-004).",
    "Z001": "The .zen file has invalid Starlark/Python syntax.",
    "Z002": "A pin references a net variable that was never created with Net()/Power()/Ground().",
    "Z003": "A .zen construct outside the supported interop subset was skipped (see ADR-002).",
    "Z004": "A Component call has no pins= dict readable at import time (missing, or built dynamically) — the part imports with no connections. Use a literal pins={...} dict.",
    "Z005": "Two .zen instances share the same name= — the netlist would be ambiguous. The first instance is kept; rename the second.",
    "Z006": "A .zen pins= dict binds the same pin key twice; the last binding wins. Remove the duplicate key.",
    "X000": "Internal compiler error surfaced by the LSP server — the server keeps running; please report the message as a bug.",
    "K001": "Two of a component's declared kinds require the same member name with different roles. Align the roles (one pin then satisfies both) or rename one kind's member.",
    "K002": "A component declares a kind but no pin binds one of that kind's required members. Add `is=<member>` to the pin that fills that role.",
    "K003": "A pin's `is=<member>` binds a kind member, but the pin's role differs from the role the kind requires for that member.",
    "K004": "A component declares a kind but is missing a required param, or the param has the wrong physical dimension.",
    "K005": "A pin binds `is=<member>` that none of the component's declared kinds defines. Fix the member name or declare the kind that provides it.",
    "B001": "A net with open-drain drivers (or an interface expecting one) has no pull-up resistor to a rail — the bus will float. Add a pull-up, or waive if it lives off-board.",
    "B002": "An `expects tie` member (strap/enable/gate pin) has no defined level: no rail, ground, resistor path, or push-pull driver on its net. Tie it, or waive with the reason.",
    "B003": "A pin that must be connected (must_connect, or a power_in pin in a wired module) is on no net — the part will be unpowered or half-functional.",
    "B004": "A net's declared voltage exceeds a pin's tolerant_to (absolute-max input) — damage-class domain mismatch. A series resistor does not fix this; use a level shifter or clamp.",
    "B005": "An `expects decoupling` power member has no decoupling capacitor to ground on its net — the classic bring-up failure (resets, brownouts).",
    "B006": "An `expects cap` member lacks its capacitor, or a regulator's input rail leaves no headroom for vout + declared v_dropout.",
    "B007": "An `expects termination` pair (RS485/CAN endpoint) has no termination resistor across it — reflections cause intermittent errors.",
    "B008": "An `expects loadcaps` crystal terminal has no load capacitor to ground, or has the wrong VALUE for the declared `cl`. Presence is not enough: the oscillator sees C_each = 2 x (cl - c_stray) (c_stray defaults to 3pF, override per part). Caps in parallel add, so the whole sum on each terminal is compared. Wrong load caps pull the frequency or stop the oscillator starting.",
    "B009": "A Polarized part is reverse-biased against the declared operating voltages (positive terminal below negative) — tantalum/electrolytic failure class.",
    "B010": "A bring-up finding was waived at instantiation (waive=\"...\") — suppressed but recorded here for review.",
    "M001": "Parts with neither mpn= nor dnp= — the BOM line is not orderable. Declare the exact orderable part (mpn=) or mark it do-not-populate (dnp=). PCB features (TestPoint, Fiducial, MountingHole...) are exempt. One summary warning per design. All M-codes run only on wired, BOM-stage designs (at least one part with mpn=/dnp=).",
    "M002": "One mpn= value is shared by parts with different value or package — an orderable part is exactly one value+footprint. Split the MPNs or align the parts.",
    "M003": "A Resistor/Capacitor/Inductor-lineage passive has no value= — an incomplete BOM line. Declare the value. One summary warning per design.",
    "M004": "A power rail (net with a power_out pin or a declared bidirectional voltage) has no TestPoint and no Connector-kind part — bring-up and ICT cannot probe it. Ground nets are exempt (probe anywhere).",
    "M005": "0402 chip passives on the board — fine-pitch assembly note (placement accuracy, rework difficulty). Informational summary.",
    "M006": "The board mixes SMT and through-hole parts — assembly needs two processes (reflow + wave/selective solder). Informational.",
    "M007": "The board has SMT parts but no Fiducial — pick-and-place optical registration needs fiducial marks. Informational.",
    "M008": "Generated packages with lead pitch <= 0.65mm (SSOP/TSSOP, or an explicit pitch=) — stencil/paste/inspection process care. Informational summary.",
}
