"""Milestone L, wave L0: `use` imports + loader, per-instance package override
(R9), ratings fallback chain, per-decl diagnostic file fidelity."""


from ohm.driver import compile_source
from ohm.emit_netlist import emit_netlist
from ohm.fmt import format_source
from ohm.loader import load, std_modules

BLOCK_LIB = (
    "# a Resistor-style library block\n"
    "component Res:\n"
    "    package = generate(CHIP0603)\n"
    "    designator_prefix = \"R\"\n"
    "    value = 0ohm\n"
    "    pin P1: role=passive, pad=1\n"
    "    pin P2: role=passive, pad=2\n"
)


def compile_at(tmp_path, name, source):
    f = tmp_path / name
    f.write_text(source)
    return compile_source(source, str(f))


# ---------- use statement + loader ----------
def test_use_std_interfaces_and_top_stays_in_entry(tmp_path):
    src = (
        "use std.interfaces\n"
        "component C:\n    package = \"X\"\n    pin SDA: role=bidirectional, pad=1\n"
        "    pin SCL: role=bidirectional, pad=2\n"
        "module Board:\n    bus = io(I2C)\n    c = C()\n"
        "    bus.sda ~ c.SDA\n    bus.scl ~ c.SCL\n"
    )
    result = compile_at(tmp_path, "board.ohm", src)
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert result.design.top == "Board"  # not a stdlib module
    assert any(i.name == "I2C" for i in result.design.interfaces)


def test_std_swapped_members_caught_via_stdlib_pair(tmp_path):
    src = (
        "use std.interfaces\n"
        "component C:\n    package = \"X\"\n    pin SDA: role=bidirectional, pad=1\n"
        "    pin SCL: role=bidirectional, pad=2\n"
        "module Board:\n    bus = io(I2C)\n    c = C()\n"
        "    bus.sda ~ c.SCL\n    bus.scl ~ c.SDA\n"
    )
    result = compile_at(tmp_path, "board.ohm", src)
    assert [d.code for d in result.diagnostics if d.code == "I005"] == ["I005", "I005"]


def test_local_import_and_dedup(tmp_path):
    (tmp_path / "lib.ohm").write_text(BLOCK_LIB)
    src = ('use "./lib.ohm"\nuse "./lib.ohm"\n'  # duplicate import is fine (dedup)
           "module M:\n    r1 = Res(value=10kohm)\n")
    result = compile_at(tmp_path, "main.ohm", src)
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert [c.name for c in result.design.components] == ["Res"]


def test_r015_unknown_import(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.nonexistent\nmodule M:\n    x = config(V, default=1V)\n")
    (d,) = [d for d in result.diagnostics if d.code == "R015"]
    assert "std.interfaces" in (d.help or "")  # lists available modules


def test_r016_import_cycle(tmp_path):
    (tmp_path / "a.ohm").write_text('use "./b.ohm"\ncomponent A:\n    package = "X"\n    pin P: role=passive, pad=1\n')
    (tmp_path / "b.ohm").write_text('use "./a.ohm"\ncomponent B:\n    package = "X"\n    pin P: role=passive, pad=1\n')
    src = 'use "./a.ohm"\nmodule M:\n    a = A()\n    b = B()\n'
    result = compile_at(tmp_path, "main.ohm", src)
    assert any(d.code == "R016" for d in result.diagnostics)
    # both components still loaded despite the cycle edge being cut
    assert {c.name for c in result.design.components} == {"A", "B"}


def test_import_diagnostics_carry_imported_file_path(tmp_path):
    bad_lib = "component Bad:\n    package = \"X\"\n    pin P: pad=1\n"  # missing role -> R006
    (tmp_path / "lib.ohm").write_text(bad_lib)
    result = compile_at(tmp_path, "main.ohm", 'use "./lib.ohm"\nmodule M:\n    b = Bad()\n')
    (d,) = [d for d in result.diagnostics if d.code == "R006"]
    assert d.file.endswith("lib.ohm") and d.span.line == 3  # the imported file's own line


def test_shared_import_from_two_files(tmp_path):
    (tmp_path / "lib.ohm").write_text(BLOCK_LIB)
    (tmp_path / "mid.ohm").write_text('use "./lib.ohm"\ncomponent Mid:\n    package = "X"\n    pin P: role=passive, pad=1\n')
    src = 'use "./lib.ohm"\nuse "./mid.ohm"\nmodule M:\n    r = Res()\n    m = Mid()\n'
    result = compile_at(tmp_path, "main.ohm", src)
    assert result.ok, [d.machine() for d in result.diagnostics]  # no R001 duplicate from dedup


def test_fmt_preserves_use_lines():
    src = 'use std.interfaces\nuse "./lib.ohm"\n\nmodule M:\n    x = config(V, default=1V)\n'
    out, diags = format_source(src)
    assert not diags
    assert out.startswith('use std.interfaces\nuse "./lib.ohm"\n')
    again, _ = format_source(out)
    assert again == out  # idempotent


def test_std_modules_listing():
    assert "interfaces" in std_modules()


def test_load_without_uses_is_plain_parse():
    prog, diags = load("module M:\n    x = config(V, default=1V)\n")
    assert not diags and len(prog.decls) == 1


# ---------- per-instance package override (R9) ----------
def test_package_override_string_footprint(tmp_path):
    (tmp_path / "lib.ohm").write_text(BLOCK_LIB)
    src = ('use "./lib.ohm"\nmodule M:\n'
           '    r1 = Res(value=10kohm, package="R_0805_2012Metric")\n'
           "    r2 = Res(value=1kohm)\n")
    result = compile_at(tmp_path, "main.ohm", src)
    assert result.ok, [d.machine() for d in result.diagnostics]
    net = emit_netlist(result.low, "x")
    assert '(footprint "R_0805_2012Metric")' in net  # r1 overridden
    assert '(footprint "ohm:CHIP0603")' in net  # r2 keeps the block default


def test_package_override_generate_spec(tmp_path):
    (tmp_path / "lib.ohm").write_text(BLOCK_LIB)
    src = ('use "./lib.ohm"\nmodule M:\n'
           "    r1 = Res(value=10kohm, package=generate(CHIP1206))\n")
    result = compile_at(tmp_path, "main.ohm", src)
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert '(footprint "ohm:CHIP1206")' in emit_netlist(result.low, "x")


def test_package_override_bad_spec_is_instance_site_error(tmp_path):
    (tmp_path / "lib.ohm").write_text(BLOCK_LIB)
    src = ('use "./lib.ohm"\nmodule M:\n'
           "    r1 = Res(package=generate(SOIC, pins=8))\n")  # pads 1..8 vs pins P1/P2
    result = compile_at(tmp_path, "main.ohm", src)
    s_codes = [d for d in result.diagnostics if d.code == "S003"]
    assert s_codes and not result.ok
    assert s_codes[-1].file.endswith("main.ohm")  # anchored at the instantiation


# ---------- ratings fallback chain ----------
def test_part_level_pmax_override_fires_q003(tmp_path):
    (tmp_path / "lib.ohm").write_text(BLOCK_LIB)
    src = (
        'use "./lib.ohm"\n'
        "component Src:\n    package = \"X\"\n"
        "    pin OUT: role=power_out, voltage=12V, pad=1\n"
        "    pin GND: role=ground, pad=2\n"
        "module M:\n"
        "    s = Src()\n"
        "    r = Res(value=100ohm, pmax=100mW)\n"  # part-level rating, no pin rating
        "    s.OUT ~ r.P1\n    r.P2 ~ s.GND\n"
    )
    result = compile_at(tmp_path, "main.ohm", src)
    assert any(d.code == "Q003" for d in result.diagnostics)  # 1.44W > 100mW


def test_part_level_vmax_fallback_fires_q001(tmp_path):
    src = (
        "component Load:\n    package = \"X\"\n    vmax = 3.6V\n"  # part-level rating
        "    pin VDD: role=power_in, pad=1\n    pin GND: role=ground, pad=2\n"
        "component Reg5:\n    package = \"X\"\n"
        "    pin OUT: role=power_out, voltage=5.0V, pad=1\n    pin GND: role=ground, pad=2\n"
        "module M:\n    u = Load()\n    reg = Reg5()\n"
        "    reg.OUT ~ u.VDD\n    reg.GND ~ u.GND\n"
    )
    result = compile_at(tmp_path, "m.ohm", src)
    hits = [d for d in result.diagnostics if d.code == "Q001"]
    assert hits, [d.machine() for d in result.diagnostics]


def test_pin_rating_still_wins_over_part_attr(tmp_path):
    src = (
        "component Load:\n    package = \"X\"\n    vmax = 3.6V\n"
        "    pin VDD: role=power_in, vmax=6.0V, pad=1\n    pin GND: role=ground, pad=2\n"
        "component Reg5:\n    package = \"X\"\n"
        "    pin OUT: role=power_out, voltage=5.0V, pad=1\n    pin GND: role=ground, pad=2\n"
        "module M:\n    u = Load()\n    reg = Reg5()\n"
        "    reg.OUT ~ u.VDD\n    reg.GND ~ u.GND\n"
    )
    result = compile_at(tmp_path, "m.ohm", src)
    assert not [d for d in result.diagnostics if d.code == "Q001"]  # pin 6.0V wins


# ---------- determinism with imports ----------
def test_double_build_with_imports_byte_identical(tmp_path):
    (tmp_path / "lib.ohm").write_text(BLOCK_LIB)
    src = 'use "./lib.ohm"\nuse std.interfaces\nmodule M:\n    r = Res(value=10kohm)\n'
    f = tmp_path / "main.ohm"
    f.write_text(src)
    a = compile_source(src, str(f))
    b = compile_source(src, str(f))
    assert emit_netlist(a.low, "x").encode() == emit_netlist(b.low, "x").encode()
    assert [d.machine() for d in a.diagnostics] == [d.machine() for d in b.diagnostics]
