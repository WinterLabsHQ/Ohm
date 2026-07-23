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
    "B008": "An `expects loadcaps` crystal terminal has no load capacitor to ground — the oscillator may not start or runs off-frequency.",
    "B009": "A Polarized part is reverse-biased against the declared operating voltages (positive terminal below negative) — tantalum/electrolytic failure class.",
    "B010": "A bring-up finding was waived at instantiation (waive=\"...\") — suppressed but recorded here for review.",
}
