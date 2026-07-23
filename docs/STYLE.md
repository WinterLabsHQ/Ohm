# Ohm — authoring guide (humans and agents)

Ohm describes circuits with **declared intent**: the more you declare, the more
the compiler catches. This guide is self-contained: everything needed to author
a correct `.ohm` file is here.

## File shape

A `.ohm` file is a sequence of top-level declarations. Python-shaped:
indentation blocks (4 spaces), `#` comments, newline-terminated statements.
Declaration order is free — forward references are fine.

```ohm
interface Power:
    v:   Signal, role=power_in
    gnd: Signal, role=ground

interface I2C:
    scl: Signal, role=bidirectional
    sda: Signal, role=bidirectional
    require pair(sda, scl)

component NCP1529:
    package = "TSOT-23-5"          # existing KiCad footprint id, OR generate(...)
    mpn = "NCP1529ASNT1G"
    pin EN:  role=input, pad=1
    pin GND: role=ground, pad=2
    pin SW:  role=power_out, pad=3
    pin VIN: role=power_in, vmax=6.0V, pad=4
    pin FB:  role=input, pad=5

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

`interface <Name>:` declares a typed bundle. Each member: `name: Signal[, role=<role>]`.
Add `require pair(a, b)` for members that miswiring commonly swaps (SDA/SCL,
D+/D−) — the compiler then catches crossed connections (error I005).

## Components

`component <Name>:` needs a `package` (a KiCad footprint id string, or
`generate(...)` — see below), optional attrs (`mpn`, `datasheet`,
`designator_prefix`, `value`), and `pin` lines.

Every pin **must** declare a `role`: one of
`power_in, power_out, input, output, bidirectional, passive, ground, nc`.
Map pins to pads with `pad=<n>`. Declare ratings where the datasheet gives
them: `vmax=`, `imax=`, `pmax=` — they enable over-voltage/current/power
checks. Declared operating points: `voltage=` on a `power_out` pin (what it
drives its net to), `current=` on a `power_in` pin (what it draws).

## Generated packages (parts missing from KiCad's library)

`package = generate(<Type>, pins=<N>[, pitch=<mm>][, density=nominal])`
generates the KiCad footprint + symbol deterministically (IPC-7351).
v1 catalog: `SOIC`, `SSOP`, `TSSOP`, `SOT23` (pins=3/5/6),
`CHIP0402/0603/0805/1206`. Every pin must have `pad=` mapping to `1..N`; the
compiler verifies the pin↔pad loop both ways (errors S002/S003).

## Modules

- `name = io(<Interface>)` — typed port.
- `name = config(<Type>, default=<value>)` — parameter; instantiation can
  override: `sub = Sub(v=3.3V)`.
- `name = <Component|Module>(<attr>=<value>, ...)` — instantiation.
- `a ~ b` — connection. **Prefer typed endpoints**: `mcu.i2c ~ sensor.i2c`
  connects whole interfaces member-by-member and is checked. Raw pin-to-pin
  (`u.P1 ~ r.P2`) is the escape hatch.
- `assert <expr>` — closed-form check at build. Use quantities
  (`3.3V`, `10kohm +/- 1%`), pin ratings (`reg.VIN.vmax`), configs, instance
  attrs (`r1.value`), and functions: `divider(vin, r_top, r_bottom)`,
  `series(...)`, `parallel(...)`, `p_vi/p_i2r/p_v2r(...)`, `rc_tau(r, c)`,
  `microstrip_z0(w, h, er)`.
- `obligation <check>(<ref>) <op> <bound>` — a convergence requirement
  (ripple, settling, EMI...) recorded for the downstream solver; never
  evaluated at build.
- `constraint <kind>(<refs...>[, <value>])` — layout constraint for the
  router: `impedance(net, 50ohm)`, `diffpair(a, b)`, `length(a, b, 0.5mm)`,
  `netclass(net, "power")`.

## Quantities

`<number><unit>` with no space: `3.3V, 100mA, 10kohm, 4.7uF, 85degC, 8MHz,
0.65mm, 100ns`. Optional tolerance: `10kohm +/- 1%` or `+/- 500ohm`.
Dimensions are checked; `3.3V + 10kohm` is a compile error.

## The standard library

`use std.<domain>` imports generic building blocks; `use "./mylib.ohm"` imports
your own. Domains: `interfaces` (I2C/SPI4/UART/USB2/CAN/RS485/SWD/JTAG/Power…),
`passives`, `discrete`, `protection`, `analog`, `power`, `logic`, `electromech`,
`connectivity`, `opto`, `sensors` (adaptation patterns).

Blocks are **categories, not parts**. A real part is an instantiation with
overrides — value, ratings, package, mpn all come from *your* datasheet:

```ohm
use std.interfaces
use std.passives

module Divider:
    vin  = io(Power)
    r1 = Resistor(value=10kohm +/- 1%, pmax=100mW)
    r2 = Resistor(value=10kohm +/- 1%, package=generate(CHIP0805))
    vin.v ~ r1.P1
    r1.P2 ~ r2.P1
    r2.P2 ~ vin.gnd
```

- `package=` is overridable per instance (string footprint or `generate(...)`);
  the compiler re-checks the pin↔pad loop for the effective package.
- Part-level rating overrides (`vmax=`, `imax=`, `pmax=`) are checked by Tier-Q.
- Blocks with concrete pads follow genuine industry-standard pinouts and say so
  in comments; where vendors vary, the comment tells you to verify. Blocks that
  have no standard pinout are `# TEMPLATE` comment blocks — copy, adapt to the
  datasheet, and the compiler checks your adaptation (the P12 flow).

## Component kinds (checked component types)

A `kind` is a *checkable contract* for a category of part — the pins (by role) and params
every real member has. Declare the kinds a component *is*, and bind each required member to
a pin with `is=`; the compiler verifies it (`use std.kinds` ships the taxonomy):

```ohm
use std.kinds

component TLV9061(OpAmp):            # this component IS an OpAmp — checked
    package = generate(SOT23, pins=5)
    pin OUT: role=output,  pad=1, is=out
    pin INP: role=input,   pad=3, is=inp
    pin INN: role=input,   pad=4, is=inn
    pin VDD: role=power_in, pad=5, is=vpos
    pin VSS: role=power_in, pad=2, is=vneg   # optional member — single-supply parts omit
```

- **Multiple kinds** compose: `component IRLZ44(MOSFET, ThermalPad):` must satisfy both.
- Missing a required member, a role that doesn't match, a missing/wrong-dimension param, or
  two kinds that demand the same member with different roles → a `K0xx` error at author time.
- **No kind = unchecked.** A `component X:` with no `(...)` is the base `Component` — declare
  a kind only when you want the contract checked. The taxonomy never claims to be exhaustive;
  define your own `kind` for anything it lacks.
- Write your own contract: `kind MySensorBus:\n    requires sda: bidirectional\n    ...`.

## Workflow

`ohm check file.ohm` — all diagnostics at once, none hidden. Machine format:
`ohm check --json-diagnostics file.ohm`. Every code is documented:
`ohm explain I005`. Fix errors until clean, then `ohm build`.

## Style rules

1. Declare every rating and role the datasheet states — declared intent is
   what makes checking possible.
2. Use typed interfaces + `require pair(...)` for any swappable signal pair.
3. Name pins exactly as the datasheet does.
4. One component per physical part; put the footprint truth in `package`.
5. Prefer `generate(...)` over hand-made footprints for parts missing from
   KiCad's library; always map every pad.
