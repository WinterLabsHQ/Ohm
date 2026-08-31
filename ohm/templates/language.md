# The Ohm language — reference

Ohm describes circuits with **declared intent**: the more you declare, the more
the compiler proves. This is a compact, self-contained reference; run
`ohm explain <CODE>` for any diagnostic you hit.

## File shape

A `.ohm` file is a sequence of top-level declarations. Python-shaped: 4-space
indentation blocks, `#` comments, one statement per line. Declaration order is
free — forward references are fine. Three top-level forms: `interface`,
`component`, `module`.

```ohm
interface Power:
    v:   Signal, role=power_in
    gnd: Signal, role=ground

component NCP1529:
    package = "TSOT-23-5"          # a KiCad footprint id, OR generate(...)
    mpn = "NCP1529ASNT1G"
    pin EN:  role=input,     pad=1
    pin GND: role=ground,    pad=2
    pin SW:  role=power_out, pad=3
    pin VIN: role=power_in,  vmax=6.0V, pad=4
    pin FB:  role=input,     pad=5

module Supply:
    vin  = io(Power)               # typed port
    vout = io(Power)
    reg  = NCP1529()
    vin.v   ~ reg.VIN
    vin.gnd ~ reg.GND
    assert vin.v.voltage <= reg.VIN.vmax
    obligation ripple(vout.v) < 50mV
```

## Interfaces

`interface <Name>:` declares a typed bundle; each member is `name: Signal[,
role=<role>]`. Add `require pair(a, b)` for signals that miswiring commonly
swaps (SDA/SCL, D+/D−) — crossed connections then become error I005.

## Components

`component <Name>:` needs a `package` (a KiCad footprint id string, or
`generate(...)`), optional attrs (`mpn`, `datasheet`, `designator_prefix`,
`value`), and `pin` lines. Every pin **must** have a `role`, one of:
`power_in, power_out, input, output, bidirectional, passive, ground, nc`.
Map pins to pads with `pad=<n>`. Declare ratings where the datasheet gives them
(`vmax=`, `imax=`, `pmax=`) to enable over-voltage/current/power checks.
Declared operating points: `voltage=` on a `power_out` pin (what it drives its
net to), `current=` on a `power_in` pin (what it draws).

Modeling convention: high-impedance analog sense pins fed by passive networks
by design (crystal pins, regulator FB/ADJ, 555 TRIG/THRES) are `role=passive`;
`role=input` is for digital logic inputs. This avoids false undriven-input
warnings.

### Never type a pinout by hand

Ohm proves a pin↔pad map is **total**, never that it is **correct**. Two swapped
pins pass every checker tier and reach copper. So take the map from a real
library instead of transcribing it:

```
ohm import-symbol /usr/share/kicad/symbols/Sensor_Motion.kicad_sym MPU-6000
```

That emits the component with real pins and stamps two attrs:

```ohm
pinout_source = "kicad:Sensor_Motion:MPU-6000"
pinout_digest = "sha256:0f8fcd36216097d0"
```

`ohm check` recomputes the digest from your declared pins, so if a pad is ever
edited afterwards you get **S008**, not a dead board. Roles, ratings and
`must_connect` are yours to change freely — the digest covers pads only.

When no library has the part, attest it by hand and say where it came from:
`pinout_source = "datasheet:DS9405 rev 9 Table 8"`. A specific part (one with
`mpn=`) carrying four or more pads and no source at all gets **S009**.

To audit a whole design against the installed libraries:
`ohm verify-pinouts <src.ohm>` for pin↔pad maps, and
`ohm verify-footprints <src.ohm>` for the pads themselves — a string
`package = "Lib:Footprint"` names pads in a library file the compiler never
opens, and KiCad renames pads between releases.

## Generated packages

`package = generate(<Type>, pins=<N>[, pitch=<mm>][, density=nominal])`
generates the KiCad footprint + symbol deterministically (IPC-7351). Catalog:
`SOIC`, `SSOP`, `TSSOP`, `SOT23` (pins=3/5/6), `CHIP0402/0603/0805/1206`,
`QFN`/`DFN`/`SON` (`body=<mm>`, optional `thermal_pad=<mm>`), `QFP` (`span=<mm>`),
`BGA`/`LGA` (`rows=`, `cols=`, `pitch=`, `ball`/`pad=`, optional `border=`),
`PLCC`, and fixed-tab `SOT223`/`SOT89`/`TO252`/`TO263`. Every pin needs a
`pad=`; the compiler verifies the pin↔pad loop both ways (errors S002/S003).

## Modules

- `name = io(<Interface>)` — a typed port.
- `name = config(<Type>, default=<value>)` — a parameter; an instantiation can
  override it: `sub = Sub(v=3.3V)`.
- `name = <Component|Module>(<attr>=<value>, ...)` — an instantiation.
- `a ~ b` — a connection. **Prefer typed endpoints** (`mcu.i2c ~ sensor.i2c`
  connects a whole interface member-by-member and is checked); raw pin-to-pin
  (`u.P1 ~ r.P2`) is the escape hatch.
- `assert <expr>` — a closed-form check folded at build. Use quantities, pin
  ratings (`reg.VIN.vmax`), configs, instance attrs (`r1.value`), and functions:
  `divider(vin, r_top, r_bottom)`, `series(...)`, `parallel(...)`,
  `p_vi/p_i2r/p_v2r(...)`, `rc_tau(r, c)`, `microstrip_z0(w, h, er)`.
- `obligation <check>(<ref>) <op> <bound>` — a convergence requirement (ripple,
  settling, EMI…) recorded for the downstream solver; never evaluated at build.
- `constraint <kind>(<refs...>[, <value>])` — a router constraint:
  `impedance(net, 50ohm)`, `diffpair(a, b)`, `length(a, b, 0.5mm)`,
  `netclass(net, "power")`, `fixed(j1, 0mm, 0mm)`, `near(c1, u1, 2mm)`.

Compile-time composition folds at elaborate, so builds stay hermetic and
byte-identical: pure `fn led_r(vcc, vf, i) = (vcc - vf) / i`, arrays and loops
(`rs = 8 * Resistor(...)`, `for i in 0..7:`), variants (`if endpoint:` / `else:`).

## Quantities

`<number><unit>` with no space: `3.3V, 100mA, 10kohm, 4.7uF, 85degC, 8MHz,
0.65mm, 100ns`. Optional tolerance: `10kohm +/- 1%` or `+/- 500ohm`. Dimensions
are checked with exact Decimal + tolerance intervals — `3.3V + 10kohm` is a
compile error.

## Standard library

`use std.<domain>` imports generic building blocks; `use "./mylib.ohm"` imports
your own (put shared blocks in `lib/`). Domains include `interfaces`
(I2C/SPI4/UART/USB2/CAN/RS485/SWD/JTAG/Power…), `passives`, `discrete`,
`protection`, `analog`, `power`, `logic`, `electromech`, `connectivity`, `opto`,
`sensors`. Blocks are **categories, not parts** — a real part is an
instantiation with your datasheet's overrides (value, ratings, package, mpn):

```ohm
use std.passives
r1 = Resistor(value=10kohm +/- 1%, pmax=100mW)
r2 = Resistor(value=10kohm +/- 1%, package=generate(CHIP0805))
```

## Component kinds

A `kind` is a checkable contract for a category of part. Declare the kinds a
component *is* and bind each required member to a pin with `is=`; the compiler
verifies it (`use std.kinds`):

```ohm
use std.kinds
component TLV9061(OpAmp):
    package = generate(SOT23, pins=5)
    pin OUT: role=output,   pad=1, is=out
    pin INP: role=input,    pad=3, is=inp
    pin INN: role=input,    pad=4, is=inn
    pin VDD: role=power_in, pad=5, is=vpos
    pin VSS: role=power_in, pad=2, is=vneg   # optional member
```

Kinds compose (`component IRLZ44(MOSFET, ThermalPad):`). A missing member, wrong
role, or missing/wrong-dimension param is a `K0xx` error. No kind = the base
`Component`, unchecked. Write your own: `kind MyBus:\n    requires sda:
bidirectional`.

## Bring-up checks and DFM lints

Interfaces and kinds declare bring-up requirements over their nets — the bugs
that pass every netlist check and die on the bench:

```ohm
interface I2C:
    expects pullup on sda, scl                      # B001 if missing
kind MOSFET:
    expects tie on gate                             # B002 if floating
interface RS485:
    expects termination(100ohm, 130ohm) on a, b when endpoint   # B007
```

Patterns: `pullup`, `decoupling(min, max)`, `loadcaps(min, max)`, `cap(min)`,
`termination(min, max)`, `tie`. `when <attr>` gates on a truthy instance attr.
Pin attrs: `must_connect=` (B003) — `true` (any net), `ground` / `rail` (a net of
that CLASS, which is what a datasheet means by "RES — connect to GND"), or
`false` to record that floating is correct. Leaving it off a specific part's pin
gets **B011**: a pin that floats by design and a pin nobody considered look the
same to every other check, so say which it is. `tolerant_to=3.6V` (B004 if a rail
exceeds it — a damage class a series R does not fix). **DFM/DFT (M-codes)**
activate once any part declares `mpn=`/`dnp=`.

Waivers are the only escape hatch: `q = NMOS(waive="tie")` turns a finding into
a visible B010 note — auditable, never silent. `--strict` still passes on a
fully-waived board.

## Workflow

`ohm import-symbol` every part you can before writing a line of wiring, then
`ohm check` — it shows all diagnostics at once, none hidden; `--json-diagnostics`
for the machine format. Fix errors until clean, then `ohm build`.
`ohm verify-pinouts <src.ohm>` audits every pad map against the installed KiCad
libraries, and `ohm verify-footprints <src.ohm>` checks that every pad a
component declares exists in the footprint that library really ships; run both
before you commit to a fab. After routing,
`ohm signoff <src.ohm> <routed.kicad_pcb>` re-verifies the board.

## Style rules

0. Never hand-type a pin↔pad map. `ohm import-symbol` it, or record where it
   came from with `pinout_source=`.
1. Declare every rating and role the datasheet states.
2. Use typed interfaces + `require pair(...)` for any swappable signal pair.
3. Name pins exactly as the datasheet does.
4. One component per physical part; put footprint truth in `package`.
5. Prefer `generate(...)` over hand-made footprints; always map every pad.
