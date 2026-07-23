"""Regression tests for W2 audit fixes (imp_zen Z004/Z005/Z006, fmt source order + comments)."""

from ohm.fmt import format_source
from ohm.imp_zen import import_zen


def nets_of(low):
    return {n.name: {(nd.part_ref, nd.pin) for nd in n.nodes} for n in low.nets}


# ---------- finding 1: missing / non-dict pins= warns Z004 ----------
def test_z004_missing_pins_dict():
    low, diags = import_zen('Component(name="U1", footprint="X")\n', "z4.zen")
    assert [d.code for d in diags] == ["Z004"]
    assert diags[0].severity == "warning" and "U1" in diags[0].message
    assert [p.ref for p in low.parts] == ["U1"]  # part still imported


def test_z004_non_dict_pins():
    low, diags = import_zen('Component(name="U2", footprint="X", pins=make_pins())\n', "z4b.zen")
    assert [d.code for d in diags] == ["Z004"]
    assert [p.ref for p in low.parts] == ["U2"]


def test_no_z004_for_empty_dict():
    _, diags = import_zen('Component(name="U3", footprint="X", pins={})\n', "z4c.zen")
    assert not any(d.code == "Z004" for d in diags)


# ---------- finding 2: duplicate instance name errors Z005, first wins ----------
def test_z005_duplicate_instance_name():
    src = (
        'vcc = Net("VCC")\n'
        'Component(name="R1", footprint="A", pins={"P1": vcc})\n'
        'Component(name="R1", footprint="B", pins={"P2": vcc})\n'
    )
    low, diags = import_zen(src, "z5.zen")
    errs = [d for d in diags if d.code == "Z005"]
    assert len(errs) == 1 and errs[0].severity == "error" and "R1" in errs[0].message
    (part,) = low.parts
    assert (part.ref, part.package) == ("R1", "A")  # first kept
    assert nets_of(low) == {"VCC": {("R1", "P1")}}  # second's pins not bound


def test_z005_across_component_and_generic():
    src = (
        'Resistor = Module("@stdlib/generics/Resistor.zen")\n'
        'vcc = Net("VCC")\n'
        'Component(name="R1", footprint="A", pins={"P1": vcc})\n'
        'Resistor(name="R1", package="0603", P1=vcc)\n'
    )
    low, diags = import_zen(src, "z5b.zen")
    assert [d.code for d in diags] == ["Z005"]
    assert [(p.ref, p.component) for p in low.parts] == [("R1", "Component")]


# ---------- finding 3: duplicate pins-dict key warns Z006, last wins ----------
def test_z006_duplicate_pin_binding_last_wins():
    src = (
        'a = Net("A")\n'
        'b = Net("B")\n'
        'Component(name="U1", footprint="X", pins={"P1": a, "P1": b})\n'
    )
    low, diags = import_zen(src, "z6.zen")
    warns = [d for d in diags if d.code == "Z006"]
    assert len(warns) == 1 and warns[0].severity == "warning" and "P1" in warns[0].message
    assert nets_of(low) == {"B": {("U1", "P1")}}  # last binding wins, A left empty


# ---------- finding 4: fmt preserves source order of interleaved items ----------
def test_fmt_preserves_component_attr_pin_order():
    src = (
        "component C:\n"
        "    pin A: role=passive\n"
        '    mpn = "X"\n'
        "    pin B: role=passive\n"
    )
    out, diags = format_source(src, "o.ohm")
    assert diags == []
    assert out == (
        "component C:\n"
        "    pin A: role=passive\n"
        '    mpn = "X"\n'
        "    pin B: role=passive\n"
    )


def test_fmt_preserves_interface_member_require_order():
    src = (
        "interface I:\n"
        "    sda: Signal, role=bidirectional\n"
        "    require pair(sda, scl)\n"
        "    scl: Signal, role=bidirectional\n"
    )
    out, diags = format_source(src, "i.ohm")
    assert diags == []
    assert out == (
        "interface I:\n"
        "    sda: Signal, role=bidirectional\n"
        "    require pair(sda, scl)\n"
        "    scl: Signal, role=bidirectional\n"
    )


# ---------- finding 5: '#' inside a string does not eat the trailing comment ----------
def test_fmt_hash_in_string_keeps_trailing_comment():
    src = (
        "component C:\n"
        '    mpn = "say # hi"  # keep me\n'
        "    pin A: role=passive\n"
    )
    out, diags = format_source(src, "s.ohm")
    assert diags == []
    assert out == (
        "component C:\n"
        '    mpn = "say # hi"  # keep me\n'
        "    pin A: role=passive\n"
    )


# ---------- finding 6: trailing comments on continuation lines are merged ----------
def test_fmt_multiline_call_merges_continuation_comments():
    src = (
        "module M:\n"
        "    a = X(  # one\n"
        "        v=1)  # two\n"
    )
    out, diags = format_source(src, "m.ohm")
    assert diags == []
    assert out == (
        "module M:\n"
        "    a = X(v=1)  # one # two\n"
    )


# ---------- finding 7: full-line comments keep the following item's indent ----------
def test_fmt_full_line_comment_indented_with_item():
    src = (
        "# top\n"
        "module M:\n"
        "    # note\n"
        "    a = X()\n"
    )
    out, diags = format_source(src, "c.ohm")
    assert diags == []
    assert out == (
        "# top\n"
        "module M:\n"
        "    # note\n"
        "    a = X()\n"
    )


# ---------- idempotence on interleaved items + comments ----------
def test_fmt_idempotent_on_interleaved_commented_fixture():
    src = (
        "# header\n"
        "interface I:\n"
        "    sda: Signal, role=bidirectional\n"
        "    require pair(sda, scl)  # pairing\n"
        "    scl: Signal, role=bidirectional\n"
        "\n"
        "component C:\n"
        "    pin A: role=passive\n"
        '    mpn = "say # hi"  # keep me\n'
        "    pin B: role=passive\n"
        "\n"
        "module M:\n"
        "    # note\n"
        "    c = C()\n"
        "    a = X(  # one\n"
        "        v=1)  # two\n"
    )
    once, diags = format_source(src, "f.ohm")
    assert diags == []
    twice, diags2 = format_source(once, "f.ohm")
    assert diags2 == []
    assert once == twice
