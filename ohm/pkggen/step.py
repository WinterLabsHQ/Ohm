"""Package body → STEP AP242 solid (ISO 10303-21).

This is the mechanical counterpart to the land pattern: the volume the part
occupies, so placement collision, height and enclosure-fit checks have something to
read. It is a *dimensional envelope* built from the body size on the datasheet —
reviewed like every other dimension — not a vendor-exact model of the plastic.
Ohm does not infer it: no body dimensions, no STEP.

A rectangular prism is written as a FACETED_BREP: six planar faces, each an
ADVANCED_FACE over a PLANE with an EDGE_LOOP of four straight EDGE_CURVEs. That is
the shape every STEP reader handles, and it needs no geometry kernel to produce.

Deterministic: entity ids are assigned in a fixed walk order, and the header carries
no timestamp, so the same body always yields byte-identical output.
"""

from __future__ import annotations

from decimal import Decimal

from ohm.formats import _HEADER_SCHEMA


def _num(value: Decimal) -> str:
    """STEP reals always carry a decimal point."""
    text = format(value.normalize(), "f")
    return text if "." in text else text + "."


class _Doc:
    """Entity table with stable, sequentially assigned ids."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, body: str) -> str:
        self.lines.append(body)
        return f"#{len(self.lines)}"


def _box_faces(x: Decimal, y: Decimal, z: Decimal, z0: Decimal):
    """8 corners and the 6 faces of an axis-aligned prism centred on X/Y.

    Each face is (outward normal, in-plane reference direction, 4 corner indices
    wound counter-clockwise as seen from outside)."""
    hx, hy = x / 2, y / 2
    z1 = z0 + z
    corners = [
        (-hx, -hy, z0), (hx, -hy, z0), (hx, hy, z0), (-hx, hy, z0),
        (-hx, -hy, z1), (hx, -hy, z1), (hx, hy, z1), (-hx, hy, z1),
    ]
    faces = [
        ((0, 0, -1), (1, 0, 0), (0, 3, 2, 1)),   # bottom (seats on the board)
        ((0, 0, 1), (1, 0, 0), (4, 5, 6, 7)),    # top
        ((0, -1, 0), (1, 0, 0), (0, 1, 5, 4)),   # front
        ((1, 0, 0), (0, 1, 0), (1, 2, 6, 5)),    # right
        ((0, 1, 0), (-1, 0, 0), (2, 3, 7, 6)),   # back
        ((-1, 0, 0), (0, -1, 0), (3, 0, 4, 7)),  # left
    ]
    return corners, faces


def emit_step_box(name: str, x: Decimal, y: Decimal, z: Decimal,
                  z0: Decimal = Decimal(0)) -> str:
    """A body `x` by `y` by `z` mm, centred on the origin, sitting `z0` above the
    board plane (0 for a body that seats on the PCB)."""
    if min(x, y, z) <= 0:
        raise ValueError(f"body dimensions must be positive, got {x}x{y}x{z}")

    d = _Doc()
    corners, faces = _box_faces(x, y, z, z0)

    def point(p) -> str:
        return d.add(f"CARTESIAN_POINT('',({_num(Decimal(p[0]))},"
                     f"{_num(Decimal(p[1]))},{_num(Decimal(p[2]))}))")

    def direction(v) -> str:
        return d.add(f"DIRECTION('',({_num(Decimal(v[0]))},"
                     f"{_num(Decimal(v[1]))},{_num(Decimal(v[2]))}))")

    pts = [point(c) for c in corners]
    verts = [d.add(f"VERTEX_POINT('',{p})") for p in pts]

    # one EDGE_CURVE per undirected corner pair, shared by the two faces meeting on it
    edges: dict[frozenset[int], str] = {}

    def edge(a: int, b: int) -> str:
        key = frozenset((a, b))
        if key not in edges:
            lo, hi = min(a, b), max(a, b)
            delta = [corners[hi][i] - corners[lo][i] for i in range(3)]
            length = max(abs(v) for v in delta)
            unit = direction([v / length for v in delta])
            vec = d.add(f"VECTOR('',{unit},{_num(length)})")
            line = d.add(f"LINE('',{pts[lo]},{vec})")
            edges[key] = d.add(
                f"EDGE_CURVE('',{verts[lo]},{verts[hi]},{line},.T.)")
        return edges[key]

    face_ids = []
    for normal, ref, loop in faces:
        origin = point(corners[loop[0]])
        axis, refdir = direction(normal), direction(ref)
        placement = d.add(f"AXIS2_PLACEMENT_3D('',{origin},{axis},{refdir})")
        plane = d.add(f"PLANE('',{placement})")
        oriented = []
        for i in range(4):
            a, b = loop[i], loop[(i + 1) % 4]
            # .T. when the shared edge runs low->high, matching EDGE_CURVE's order
            oriented.append(d.add(
                f"ORIENTED_EDGE('',*,*,{edge(a, b)},{'.T.' if a < b else '.F.'})"))
        edge_loop = d.add(f"EDGE_LOOP('',({','.join(oriented)}))")
        bound = d.add(f"FACE_OUTER_BOUND('',{edge_loop},.T.)")
        face_ids.append(d.add(f"ADVANCED_FACE('',({bound}),{plane},.T.)"))

    shell = d.add(f"CLOSED_SHELL('',({','.join(face_ids)}))")
    brep = d.add(f"MANIFOLD_SOLID_BREP('{name}',{shell})")

    # units + context
    length_unit = d.add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    angle_unit = d.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    solid_unit = d.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    uncertainty = d.add(
        f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),{length_unit},"
        "'distance_accuracy_value','confusion accuracy')")
    context = d.add(
        "( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({uncertainty})) "
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({length_unit},{angle_unit},{solid_unit})) "
        "REPRESENTATION_CONTEXT('Context #1',"
        "'3D Context with UNIT and UNCERTAINTY') )")

    origin = point((Decimal(0), Decimal(0), Decimal(0)))
    axis = direction((Decimal(0), Decimal(0), Decimal(1)))
    refdir = direction((Decimal(1), Decimal(0), Decimal(0)))
    placement = d.add(f"AXIS2_PLACEMENT_3D('',{origin},{axis},{refdir})")
    shape = d.add("ADVANCED_BREP_SHAPE_REPRESENTATION('"
                  f"{name}',({placement},{brep}),{context})")

    # AP242's identifying triple, and it has to agree with FILE_SCHEMA above or the
    # file claims one protocol in the header and another in the data. All three
    # strings are copied verbatim out of OpenCASCADE's own AP242 write lane
    # (STEPConstruct_ContextTool.cxx, AddAPD(), `mySchema == 5`), for the same
    # reason the Altium constants in ohm/formats.py are copied off an
    # Altium-authored file: what the tool itself writes is a fact, what we compose
    # from prose is a guess. This is not a new habit — the AP214 triple these lines
    # replace was OCC's `mySchema == 4` lane, right down to the 2000.
    #
    # Two of the strings are load-bearing in opposite ways:
    #   * 'ap242_managed_model_based_3d_engineering' — note NO `_mim_lf` suffix,
    #     unlike the FILE_SCHEMA name. OCC's STEPConstruct_ContextTool::IsAP242()
    #     lower-cases this field and compares it against exactly that spelling, so
    #     "correcting" it to match FILE_SCHEMA would make OCC stop recognising the
    #     file as AP242 on read-back.
    #   * 2013 is the year OCC stamps: it dates from the AP242 DIS that OCC
    #     implemented, where ISO published edition 1 in 2014. Nothing reads the
    #     year — it is descriptive — so it is left agreeing with the one AP242
    #     writer Ohm's output can actually be diffed against rather than silently
    #     diverging from it.
    app_context = d.add("APPLICATION_CONTEXT("
                        "'Managed model based 3d engineering')")
    d.add(f"APPLICATION_PROTOCOL_DEFINITION('international standard',"
          f"'ap242_managed_model_based_3d_engineering',2013,{app_context})")
    product_context = d.add(f"PRODUCT_CONTEXT('',{app_context},'mechanical')")
    product = d.add(f"PRODUCT('{name}','{name}','',({product_context}))")
    formation = d.add(f"PRODUCT_DEFINITION_FORMATION('','',{product})")
    def_context = d.add(
        f"PRODUCT_DEFINITION_CONTEXT('part definition',{app_context},'design')")
    definition = d.add(f"PRODUCT_DEFINITION('design','',{formation},{def_context})")
    def_shape = d.add(f"PRODUCT_DEFINITION_SHAPE('','',{definition})")
    d.add(f"SHAPE_DEFINITION_REPRESENTATION({def_shape},{shape})")
    d.add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,({product}))")

    body = "\n".join(f"#{i + 1} = {line};" for i, line in enumerate(d.lines))
    return (
        "ISO-10303-21;\n"
        "HEADER;\n"
        f"FILE_DESCRIPTION(('{name} body envelope'),'2;1');\n"
        # no timestamp: the output has to stay byte-identical across runs
        f"FILE_NAME('{name}.step','1970-01-01T00:00:00',('ohm'),(''),"
        "'ohm pkggen','ohm','');\n"
        f"FILE_SCHEMA(('{_HEADER_SCHEMA}'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        f"{body}\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )
