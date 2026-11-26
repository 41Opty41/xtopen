"""Clean-room synthetic Parasolid XT B-rep fixtures for xtopen unit tests."""

from __future__ import annotations

import math
from typing import Any

import pytest

from xtopen.schema import builtin_schema, effective
from xtopen.xb import ParseResult, XtHeader, XtNode


class _XtBrepBuilder:
    """Helper to assemble valid synthetic Parasolid XT node graphs cleanly."""

    def __init__(self) -> None:
        self._schema = builtin_schema()
        self._nodes: list[XtNode] = []
        self._next_idx = 1

    def alloc(self) -> int:
        idx = self._next_idx
        self._next_idx += 1
        return idx

    def add(self, ntype: int, idx: int | None = None, *, varlen: int | None = None, **values: Any) -> int:
        if idx is None:
            idx = self.alloc()
        snode = self._schema[ntype]
        fields: list[tuple[str, Any]] = []
        for f in effective(snode):
            fname = f["name"]
            if fname in values:
                val = values[fname]
                if f["code"] == "c" and isinstance(val, str):
                    val = [ord(ch) for ch in val]
                elif f["elements"] == 0 and f["code"] in ("i", "v", "h", "b"):
                    if isinstance(val, list) and val and not isinstance(val[0], list):
                        val = [val]
                    elif not isinstance(val, list):
                        val = [val]
                elif not isinstance(val, list):
                    val = [val]
            else:
                if f["name"] == "node_id":
                    val = [idx]
                elif f["elements"] == 1:
                    val = []
                elif f["elements"] > 1:
                    val = [0] * f["elements"]
                elif f["code"] == "p":
                    val = [None]
                elif f["code"] in ("u", "n", "w", "d", "t"):
                    val = [0]
                elif f["code"] == "f":
                    val = [0.0]
                elif f["code"] == "l":
                    val = [False]
                elif f["code"] == "c":
                    val = [ord("+")]
                else:
                    val = [0]
            fields.append((fname, val))
        self._nodes.append(XtNode(type=ntype, name=snode["name"], index=idx, fields=fields, varlen=varlen))
        return idx

    def point(self, x: float, y: float, z: float, owner: int = 1) -> int:
        return self.add(29, pvec=[float(x), float(y), float(z)], owner=owner)

    def vertex(self, pt_idx: int, owner: int = 1) -> int:
        return self.add(18, point=pt_idx, owner=owner, tolerance=1e-7)

    def line(self, p: tuple[float, float, float], d: tuple[float, float, float], owner: int = 1) -> int:
        return self.add(30, pvec=list(p), direction=list(d), owner=owner, sense="+")

    def circle(
        self,
        centre: tuple[float, float, float],
        normal: tuple[float, float, float],
        x_axis: tuple[float, float, float],
        radius: float,
        owner: int = 1,
    ) -> int:
        return self.add(
            31,
            centre=list(centre),
            normal=list(normal),
            x_axis=list(x_axis),
            radius=float(radius),
            owner=owner,
            sense="+",
        )

    def ellipse(
        self,
        centre: tuple[float, float, float],
        normal: tuple[float, float, float],
        x_axis: tuple[float, float, float],
        rmaj: float,
        rmin: float,
        owner: int = 1,
    ) -> int:
        return self.add(
            32,
            centre=list(centre),
            normal=list(normal),
            x_axis=list(x_axis),
            major_radius=float(rmaj),
            minor_radius=float(rmin),
            owner=owner,
            sense="+",
        )

    def plane(
        self,
        pvec: tuple[float, float, float],
        normal: tuple[float, float, float],
        x_axis: tuple[float, float, float],
        owner: int = 1,
    ) -> int:
        return self.add(
            50,
            pvec=list(pvec),
            normal=list(normal),
            x_axis=list(x_axis),
            owner=owner,
            sense="+",
        )

    def build_solid(
        self,
        *,
        body_idx: int,
        shell_idx: int,
        region_idx: int,
        body_type: int = 1,
    ) -> ParseResult:
        self.add(
            12,
            idx=body_idx,
            highest_node_id=self._next_idx + 10,
            res_size=1000.0,
            res_linear=1e-8,
            state=1,
            body_type=body_type,
            nom_geom_state=1,
            shell=shell_idx,
            region=region_idx,
        )
        first_face = next((n.index for n in self._nodes if n.type == 14), None)
        self.add(13, idx=shell_idx, body=body_idx, face=first_face, front_face=first_face, region=region_idx)
        self.add(19, idx=region_idx, body=body_idx, shell=shell_idx, type="S")
        # Order nodes with BODY first as in standard Parasolid streams
        ordered = sorted(self._nodes, key=lambda n: n.index)
        hdr = XtHeader(
            format_kind="binary",
            modeler="xtopen synthetic test fixture",
            schema_name="sch_12000",
            form2=False,
        )
        return ParseResult(header=hdr, nodes=ordered, remaining_bytes=0)


def _wire_halfedges_in_loop(
    b: _XtBrepBuilder,
    loop_idx: int,
    spec: list[tuple[int, int, str]],
    edge_fins: dict[int, list[tuple[int, str]]],
) -> int:
    """Create circular loop of HALFEDGE nodes for [(edge_idx, start_vtx_idx, sense), ...]."""
    fin_ids = [b.alloc() for _ in spec]
    n = len(spec)
    for i, (e_idx, _v_start, sense) in enumerate(spec):
        fid = fin_ids[i]
        edge_fins.setdefault(e_idx, []).append((fid, sense))
        end_vtx = spec[(i + 1) % n][1]
        b.add(
            17,
            idx=fid,
            loop=loop_idx,
            forward=fin_ids[(i + 1) % n],
            backward=fin_ids[(i - 1) % n],
            vertex=end_vtx,
            edge=e_idx,
            sense=sense,
        )
    return fin_ids[0]


def make_box(dx: float = 1.0, dy: float = 2.0, dz: float = 3.0) -> ParseResult:
    """Build a closed axis-aligned box [0, dx] x [0, dy] x [0, dz] (meters)."""
    b = _XtBrepBuilder()
    body_idx, shell_idx, region_idx = b.alloc(), b.alloc(), b.alloc()

    coords = [
        (0.0, 0.0, 0.0), (dx, 0.0, 0.0), (dx, dy, 0.0), (0.0, dy, 0.0),
        (0.0, 0.0, dz), (dx, 0.0, dz), (dx, dy, dz), (0.0, dy, dz),
    ]
    verts = [b.vertex(b.point(*c, owner=body_idx), owner=body_idx) for c in coords]

    edge_defs = [
        (0, 1, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),   # 0: bottom y=0
        (1, 2, (dx, 0.0, 0.0), (0.0, 1.0, 0.0)),    # 1: bottom x=dx
        (2, 3, (dx, dy, 0.0), (-1.0, 0.0, 0.0)),    # 2: bottom y=dy
        (3, 0, (0.0, dy, 0.0), (0.0, -1.0, 0.0)),   # 3: bottom x=0
        (4, 5, (0.0, 0.0, dz), (1.0, 0.0, 0.0)),    # 4: top y=0
        (5, 6, (dx, 0.0, dz), (0.0, 1.0, 0.0)),     # 5: top x=dx
        (6, 7, (dx, dy, dz), (-1.0, 0.0, 0.0)),     # 6: top y=dy
        (7, 4, (0.0, dy, dz), (0.0, -1.0, 0.0)),    # 7: top x=0
        (0, 4, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),   # 8: z-pillar 0
        (1, 5, (dx, 0.0, 0.0), (0.0, 0.0, 1.0)),    # 9: z-pillar 1
        (2, 6, (dx, dy, 0.0), (0.0, 0.0, 1.0)),     # 10: z-pillar 2
        (3, 7, (0.0, dy, 0.0), (0.0, 0.0, 1.0)),    # 11: z-pillar 3
    ]
    edges = [b.add(16, curve=b.line(o, d, owner=body_idx), owner=body_idx) for _, _, o, d in edge_defs]

    # CCW when viewed from outside (+normal)
    face_specs = [
        ((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), [(0, 3, "-"), (3, 0, "-"), (2, 1, "-"), (1, 2, "-")], [(3, 0, "-"), (2, 3, "-"), (1, 2, "-"), (0, 1, "-")]),
    ]
    # Build exact CCW loops manually:
    # Bottom z=0 (normal -Z): looking from -Z, +X right, -Y up -> 0 -> 3 -> 2 -> 1 -> 0
    loops_ccw = [
        # (origin, normal, x_axis, [(edge_i, start_vert_i, sense), ...])
        ((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), [(3, 0, "-"), (2, 3, "-"), (1, 2, "-"), (0, 1, "-")]),
        ((0.0, 0.0, dz), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), [(4, 4, "+"), (5, 5, "+"), (6, 6, "+"), (7, 7, "+")]),
        ((0.0, 0.0, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), [(0, 0, "+"), (9, 1, "+"), (4, 5, "-"), (8, 4, "-")]),
        ((dx, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), [(1, 1, "+"), (10, 2, "+"), (5, 6, "-"), (9, 5, "-")]),
        ((0.0, dy, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), [(2, 2, "+"), (11, 3, "+"), (6, 7, "-"), (10, 6, "-")]),
        ((0.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), [(3, 3, "+"), (8, 0, "+"), (7, 4, "-"), (11, 7, "-")]),
    ]

    edge_fins: dict[int, list[tuple[int, str]]] = {}
    for orig, nrm, xax, ce_spec in loops_ccw:
        f_idx = b.alloc()
        l_idx = b.alloc()
        s_idx = b.plane(orig, nrm, xax, owner=f_idx)
        first_fin = _wire_halfedges_in_loop(
            b, l_idx, [(edges[ei], verts[vi], s) for ei, vi, s in ce_spec], edge_fins
        )
        b.add(15, idx=l_idx, face=f_idx, halfedge=first_fin)
        b.add(14, idx=f_idx, shell=shell_idx, front_shell=shell_idx, surface=s_idx, loop=l_idx, sense="+")

    for e_idx, fins in edge_fins.items():
        node = next(n for n in b._nodes if n.index == e_idx)
        for k, (fk, fv) in enumerate(node.fields):
            if fk == "halfedge":
                node.fields[k] = ("halfedge", [fins[0][0]])
        if len(fins) == 2:
            f0, f1 = fins[0][0], fins[1][0]
            for fa, fb in ((f0, f1), (f1, f0)):
                fn = next(n for n in b._nodes if n.index == fa)
                for k, (fk, _) in enumerate(fn.fields):
                    if fk == "other":
                        fn.fields[k] = ("other", [fb])
    return b.build_solid(body_idx=body_idx, shell_idx=shell_idx, region_idx=region_idx)


def make_cylinder(r: float = 0.5, h: float = 2.0) -> ParseResult:
    """Build a closed solid cylinder of radius r and height h centered on Z=[0, h]."""
    b = _XtBrepBuilder()
    body_idx, shell_idx, region_idx = b.alloc(), b.alloc(), b.alloc()

    # Split cylindrical barrel into two semicircular halves at +X and -X so every face has valid 4-edge loops
    v0 = b.vertex(b.point(r, 0.0, 0.0, owner=body_idx), owner=body_idx)
    v1 = b.vertex(b.point(-r, 0.0, 0.0, owner=body_idx), owner=body_idx)
    v2 = b.vertex(b.point(r, 0.0, h, owner=body_idx), owner=body_idx)
    v3 = b.vertex(b.point(-r, 0.0, h, owner=body_idx), owner=body_idx)

    # Bottom circle (+Z normal, +X x_axis -> +Y mid): e_bot1: v0->v1, e_bot2: v1->v0
    c_bot = b.circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), r, owner=body_idx)
    e_b1 = b.add(16, curve=c_bot, owner=body_idx)
    e_b2 = b.add(16, curve=c_bot, owner=body_idx)
    # Top circle (+Z normal, +X x_axis): e_top1: v2->v3, e_top2: v3->v2
    c_top = b.circle((0.0, 0.0, h), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), r, owner=body_idx)
    e_t1 = b.add(16, curve=c_top, owner=body_idx)
    e_t2 = b.add(16, curve=c_top, owner=body_idx)
    # Two vertical seam lines
    e_s0 = b.add(16, curve=b.line((r, 0.0, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx)
    e_s1 = b.add(16, curve=b.line((-r, 0.0, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx)

    edge_fins: dict[int, list[tuple[int, str]]] = {}

    def add_face(surf_idx: int, ce_spec: list[tuple[int, int, str]]) -> None:
        f_idx, l_idx = b.alloc(), b.alloc()
        fin0 = _wire_halfedges_in_loop(b, l_idx, ce_spec, edge_fins)
        b.add(15, idx=l_idx, face=f_idx, halfedge=fin0)
        b.add(14, idx=f_idx, shell=shell_idx, front_shell=shell_idx, surface=surf_idx, loop=l_idx, sense="+")

    # Bottom planar cap (normal -Z -> CW in XY = e_b2 - (v0->v1), e_b1 - (v1->v0))
    add_face(
        b.plane((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
        [(e_b2, v0, "-"), (e_b1, v1, "-")],
    )
    # Top planar cap (normal +Z -> CCW in XY = e_t1 + (v2->v3), e_t2 + (v3->v2))
    add_face(
        b.plane((0.0, 0.0, h), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        [(e_t1, v2, "+"), (e_t2, v3, "+")],
    )
    # Cylindrical barrel half 1 (u in [0, pi], y >= 0) & half 2 (u in [pi, 2pi], y <= 0)
    cyl = b.add(51, pvec=[0.0, 0.0, 0.0], axis=[0.0, 0.0, 1.0], radius=r, x_axis=[1.0, 0.0, 0.0], sense="+")
    add_face(cyl, [(e_b1, v0, "+"), (e_s1, v1, "+"), (e_t1, v3, "-"), (e_s0, v2, "-")])
    add_face(cyl, [(e_b2, v1, "+"), (e_s0, v0, "+"), (e_t2, v2, "-"), (e_s1, v3, "-")])

    return b.build_solid(body_idx=body_idx, shell_idx=shell_idx, region_idx=region_idx)


def make_cone_frustum(r1: float = 1.0, r2: float = 0.4, h: float = 2.0) -> ParseResult:
    """Build a closed conical frustum (bottom radius r1 at z=0, top radius r2 at z=h)."""
    b = _XtBrepBuilder()
    body_idx, shell_idx, region_idx = b.alloc(), b.alloc(), b.alloc()

    v0 = b.vertex(b.point(r1, 0.0, 0.0, owner=body_idx), owner=body_idx)
    v1 = b.vertex(b.point(-r1, 0.0, 0.0, owner=body_idx), owner=body_idx)
    v2 = b.vertex(b.point(r2, 0.0, h, owner=body_idx), owner=body_idx)
    v3 = b.vertex(b.point(-r2, 0.0, h, owner=body_idx), owner=body_idx)

    c_bot = b.circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), r1, owner=body_idx)
    e_b1 = b.add(16, curve=c_bot, owner=body_idx)
    e_b2 = b.add(16, curve=c_bot, owner=body_idx)
    c_top = b.circle((0.0, 0.0, h), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), r2, owner=body_idx)
    e_t1 = b.add(16, curve=c_top, owner=body_idx)
    e_t2 = b.add(16, curve=c_top, owner=body_idx)

    dr = r1 - r2
    sl = math.sqrt(dr * dr + h * h)
    # In Parasolid CONE(52), axis points toward smaller end (apex) when sin_half_angle > 0
    sin_ha = dr / sl
    cos_ha = h / sl
    d0 = b.line((r1, 0.0, 0.0), (-dr / sl, 0.0, h / sl), owner=body_idx)
    d1 = b.line((-r1, 0.0, 0.0), (dr / sl, 0.0, h / sl), owner=body_idx)
    e_s0 = b.add(16, curve=d0, owner=body_idx)
    e_s1 = b.add(16, curve=d1, owner=body_idx)

    edge_fins: dict[int, list[tuple[int, str]]] = {}

    def add_face(surf_idx: int, ce_spec: list[tuple[int, int, str]]) -> None:
        f_idx, l_idx = b.alloc(), b.alloc()
        fin0 = _wire_halfedges_in_loop(b, l_idx, ce_spec, edge_fins)
        b.add(15, idx=l_idx, face=f_idx, halfedge=fin0)
        b.add(14, idx=f_idx, shell=shell_idx, front_shell=shell_idx, surface=surf_idx, loop=l_idx, sense="+")

    add_face(b.plane((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0)), [(e_b2, v0, "-"), (e_b1, v1, "-")])
    add_face(b.plane((0.0, 0.0, h), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)), [(e_t1, v2, "+"), (e_t2, v3, "+")])
    cone_s = b.add(
        52,
        pvec=[0.0, 0.0, 0.0],
        axis=[0.0, 0.0, 1.0],
        radius=r1,
        sin_half_angle=sin_ha,
        cos_half_angle=cos_ha,
        x_axis=[1.0, 0.0, 0.0],
        sense="+",
    )
    add_face(cone_s, [(e_b1, v0, "+"), (e_s1, v1, "+"), (e_t1, v3, "-"), (e_s0, v2, "-")])
    add_face(cone_s, [(e_b2, v1, "+"), (e_s0, v0, "+"), (e_t2, v2, "-"), (e_s1, v3, "-")])

    return b.build_solid(body_idx=body_idx, shell_idx=shell_idx, region_idx=region_idx)


def make_plate_with_hole(w: float = 4.0, d: float = 4.0, h: float = 1.0, hole_r: float = 0.5) -> ParseResult:
    """Build a solid rectangular plate [-w/2, w/2] x [-d/2, d/2] x [0, h] with central cylindrical through-hole."""
    b = _XtBrepBuilder()
    body_idx, shell_idx, region_idx = b.alloc(), b.alloc(), b.alloc()
    hx, hy = w / 2.0, d / 2.0

    # 8 outer box vertices
    ov = [
        b.vertex(b.point(px, py, pz, owner=body_idx), owner=body_idx)
        for pz in (0.0, h)
        for px, py in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))
    ]
    outer_edges = [
        b.add(16, curve=b.line((-hx, -hy, 0.0), (1.0, 0.0, 0.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((hx, -hy, 0.0), (0.0, 1.0, 0.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((hx, hy, 0.0), (-1.0, 0.0, 0.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((-hx, hy, 0.0), (0.0, -1.0, 0.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((-hx, -hy, h), (1.0, 0.0, 0.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((hx, -hy, h), (0.0, 1.0, 0.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((hx, hy, h), (-1.0, 0.0, 0.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((-hx, hy, h), (0.0, -1.0, 0.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((-hx, -hy, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((hx, -hy, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((hx, hy, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx),
        b.add(16, curve=b.line((-hx, hy, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx),
    ]

    # Hole vertices + circular edges + seam vertical edges
    hv0 = b.vertex(b.point(hole_r, 0.0, 0.0, owner=body_idx), owner=body_idx)
    hv1 = b.vertex(b.point(-hole_r, 0.0, 0.0, owner=body_idx), owner=body_idx)
    hv2 = b.vertex(b.point(hole_r, 0.0, h, owner=body_idx), owner=body_idx)
    hv3 = b.vertex(b.point(-hole_r, 0.0, h, owner=body_idx), owner=body_idx)
    hc_bot = b.circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), hole_r, owner=body_idx)
    hc_top = b.circle((0.0, 0.0, h), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), hole_r, owner=body_idx)
    hb1 = b.add(16, curve=hc_bot, owner=body_idx)
    hb2 = b.add(16, curve=hc_bot, owner=body_idx)
    ht1 = b.add(16, curve=hc_top, owner=body_idx)
    ht2 = b.add(16, curve=hc_top, owner=body_idx)
    hs0 = b.add(16, curve=b.line((hole_r, 0.0, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx)
    hs1 = b.add(16, curve=b.line((-hole_r, 0.0, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx)

    edge_fins: dict[int, list[tuple[int, str]]] = {}

    def add_multi_face(surf_idx: int, loops_spec: list[list[tuple[int, int, str]]], sense: str = "+") -> None:
        f_idx = b.alloc()
        loop_ids = [b.alloc() for _ in loops_spec]
        for i, l_idx in enumerate(loop_ids):
            fin0 = _wire_halfedges_in_loop(b, l_idx, loops_spec[i], edge_fins)
            nxt = loop_ids[i + 1] if i + 1 < len(loop_ids) else None
            b.add(15, idx=l_idx, face=f_idx, halfedge=fin0, next=nxt)
        b.add(14, idx=f_idx, shell=shell_idx, front_shell=shell_idx, surface=surf_idx, loop=loop_ids[0], sense=sense)

    # Bottom z=0 (normal -Z): outer CW in XY, inner hole CCW in XY (material to left -> solid is OUTSIDE hole!)
    add_multi_face(
        b.plane((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
        [
            [(outer_edges[3], ov[0], "-"), (outer_edges[2], ov[3], "-"), (outer_edges[1], ov[2], "-"), (outer_edges[0], ov[1], "-")],
            [(hb1, hv0, "+"), (hb2, hv1, "+")],
        ],
    )
    # Top z=h (normal +Z): outer CCW in XY, inner hole CW in XY
    add_multi_face(
        b.plane((0.0, 0.0, h), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        [
            [(outer_edges[4], ov[4], "+"), (outer_edges[5], ov[5], "+"), (outer_edges[6], ov[6], "+"), (outer_edges[7], ov[7], "+")],
            [(ht2, hv2, "-"), (ht1, hv3, "-")],
        ],
    )
    # 4 outer sides
    add_multi_face(b.plane((-hx, -hy, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)), [[(outer_edges[0], ov[0], "+"), (outer_edges[9], ov[1], "+"), (outer_edges[4], ov[5], "-"), (outer_edges[8], ov[4], "-")]])
    add_multi_face(b.plane((hx, -hy, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), [[(outer_edges[1], ov[1], "+"), (outer_edges[10], ov[2], "+"), (outer_edges[5], ov[6], "-"), (outer_edges[9], ov[5], "-")]])
    add_multi_face(b.plane((-hx, hy, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)), [[(outer_edges[2], ov[2], "+"), (outer_edges[11], ov[3], "+"), (outer_edges[6], ov[7], "-"), (outer_edges[10], ov[6], "-")]])
    add_multi_face(b.plane((-hx, -hy, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)), [[(outer_edges[3], ov[3], "+"), (outer_edges[8], ov[0], "+"), (outer_edges[7], ov[4], "-"), (outer_edges[11], ov[7], "-")]])

    # Inner hole cylindrical wall (face sense '-' so normal points inward into hole void!)
    hcyl = b.add(51, pvec=[0.0, 0.0, 0.0], axis=[0.0, 0.0, 1.0], radius=hole_r, x_axis=[1.0, 0.0, 0.0], sense="+")
    add_multi_face(hcyl, [[(hb1, hv1, "-"), (hs0, hv0, "+"), (ht1, hv2, "+"), (hs1, hv3, "-")]], sense="-")
    add_multi_face(hcyl, [[(hb2, hv0, "-"), (hs1, hv1, "+"), (ht2, hv3, "+"), (hs0, hv2, "-")]], sense="-")

    return b.build_solid(body_idx=body_idx, shell_idx=shell_idx, region_idx=region_idx)


def make_sphere_shell(r: float = 1.0) -> ParseResult:
    """Build a closed spherical solid split into two hemispheres along z=0."""
    b = _XtBrepBuilder()
    body_idx, shell_idx, region_idx = b.alloc(), b.alloc(), b.alloc()
    v0 = b.vertex(b.point(r, 0.0, 0.0, owner=body_idx), owner=body_idx)
    v1 = b.vertex(b.point(-r, 0.0, 0.0, owner=body_idx), owner=body_idx)
    eq = b.circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), r, owner=body_idx)
    e1 = b.add(16, curve=eq, owner=body_idx)
    e2 = b.add(16, curve=eq, owner=body_idx)
    sph = b.add(53, centre=[0.0, 0.0, 0.0], radius=r, axis=[0.0, 0.0, 1.0], x_axis=[1.0, 0.0, 0.0], sense="+")

    edge_fins: dict[int, list[tuple[int, str]]] = {}
    # Upper hemisphere (z >= 0) bounded CCW from outside by e1+ (v0->v1), e2+ (v1->v0)
    f1, l1 = b.alloc(), b.alloc()
    fin1 = _wire_halfedges_in_loop(b, l1, [(e1, v0, "+"), (e2, v1, "+")], edge_fins)
    b.add(15, idx=l1, face=f1, halfedge=fin1)
    b.add(14, idx=f1, shell=shell_idx, front_shell=shell_idx, surface=sph, loop=l1, sense="+")
    # Lower hemisphere (z <= 0) bounded CW in XY
    f2, l2 = b.alloc(), b.alloc()
    fin2 = _wire_halfedges_in_loop(b, l2, [(e2, v0, "-"), (e1, v1, "-")], edge_fins)
    b.add(15, idx=l2, face=f2, halfedge=fin2)
    b.add(14, idx=f2, shell=shell_idx, front_shell=shell_idx, surface=sph, loop=l2, sense="+")
    return b.build_solid(body_idx=body_idx, shell_idx=shell_idx, region_idx=region_idx)


def make_torus_sheet(rmaj: float = 1.0, rmin: float = 0.25) -> ParseResult:
    """Build a synthetic toroidal patch (sheet body)."""
    b = _XtBrepBuilder()
    body_idx, shell_idx, region_idx = b.alloc(), b.alloc(), b.alloc()
    R_out = rmaj + rmin
    v0 = b.vertex(b.point(R_out, 0.0, 0.0, owner=body_idx), owner=body_idx)
    v1 = b.vertex(b.point(0.0, R_out, 0.0, owner=body_idx), owner=body_idx)
    v2 = b.vertex(b.point(0.0, rmaj, rmin, owner=body_idx), owner=body_idx)
    v3 = b.vertex(b.point(rmaj, 0.0, rmin, owner=body_idx), owner=body_idx)

    e_eq = b.add(16, curve=b.circle((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), R_out, owner=body_idx))
    e_m1 = b.add(16, curve=b.circle((0.0, rmaj, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), rmin, owner=body_idx))
    e_top = b.add(16, curve=b.circle((0.0, 0.0, rmin), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), rmaj, owner=body_idx))
    e_m0 = b.add(16, curve=b.circle((rmaj, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0), rmin, owner=body_idx))

    tor = b.add(54, centre=[0.0, 0.0, 0.0], axis=[0.0, 0.0, 1.0], major_radius=rmaj, minor_radius=rmin, x_axis=[1.0, 0.0, 0.0], sense="+")
    edge_fins: dict[int, list[tuple[int, str]]] = {}
    f1, l1 = b.alloc(), b.alloc()
    fin0 = _wire_halfedges_in_loop(b, l1, [(e_eq, v0, "+"), (e_m1, v1, "+"), (e_top, v2, "-"), (e_m0, v3, "-")], edge_fins)
    b.add(15, idx=l1, face=f1, halfedge=fin0)
    b.add(14, idx=f1, shell=shell_idx, front_shell=shell_idx, surface=tor, loop=l1, sense="+")
    return b.build_solid(body_idx=body_idx, shell_idx=shell_idx, region_idx=region_idx, body_type=2)


def make_rational_nurbs_patch() -> ParseResult:
    """Build a rational quadratic NURBS quarter-cylinder surface patch with a 2D p-curve edge."""
    b = _XtBrepBuilder()
    body_idx, shell_idx, region_idx = b.alloc(), b.alloc(), b.alloc()

    w = math.sqrt(0.5)
    # 3x2 rational NURBS quarter-cylinder of radius 1, height 1 along Z:
    # u in [0, 1]: [(1,0,z), w*(1,1,z), (0,1,z)] ; v in [0, 1]: z = 0, z = 1
    poles_flat = [
        1.0, 0.0, 0.0, 1.0,
        1.0, 0.0, 1.0, 1.0,
        w * 1.0, w * 1.0, 0.0, w,
        w * 1.0, w * 1.0, w * 1.0, w,
        0.0, 1.0, 0.0, 1.0,
        0.0, 1.0, 1.0, 1.0,
    ]
    bv = b.add(45, vertices=poles_flat, varlen=len(poles_flat))
    um = b.add(127, mult=[3, 3], varlen=2)
    vm = b.add(127, mult=[2, 2], varlen=2)
    uk = b.add(128, knots=[0.0, 1.0], varlen=2)
    vk = b.add(128, knots=[0.0, 1.0], varlen=2)
    nurbs_s = b.add(
        126,
        u_periodic=False, v_periodic=False, u_degree=2, v_degree=1,
        n_u_vertices=3, n_v_vertices=2,
        n_u_knots=2, n_v_knots=2,
        rational=True, u_closed=False, v_closed=False, vertex_dim=4,
        bspline_vertices=bv, u_knot_mult=um, v_knot_mult=vm, u_knots=uk, v_knots=vk,
    )
    bsurf = b.add(124, nurbs=nurbs_s, sense="+", owner=body_idx)

    # Also build a smooth 2D p-curve edge on a planar triangle (exact area = 0.5) for vertex_dim=2 testing
    pl = b.plane((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), owner=body_idx)
    bv2 = b.add(45, vertices=[0.0, 0.0, 0.5, 0.0, 1.0, 0.0], varlen=6)
    m2 = b.add(127, mult=[3, 3], varlen=2)
    k2 = b.add(128, knots=[0.0, 1.0], varlen=2)
    nc2 = b.add(
        136,
        degree=2, n_vertices=3, vertex_dim=2, n_knots=2,
        periodic=False, closed=False, rational=False,
        bspline_vertices=bv2, knot_mult=m2, knots=k2,
    )
    bc2 = b.add(134, nurbs=nc2, sense="+", owner=body_idx)
    sp2 = b.add(137, surface=pl, b_curve=bc2, sense="+", owner=body_idx)

    v0 = b.vertex(b.point(1.0, 0.0, 0.0, owner=body_idx), owner=body_idx)
    v1 = b.vertex(b.point(0.0, 1.0, 0.0, owner=body_idx), owner=body_idx)
    v2 = b.vertex(b.point(0.0, 1.0, 1.0, owner=body_idx), owner=body_idx)
    v3 = b.vertex(b.point(1.0, 0.0, 1.0, owner=body_idx), owner=body_idx)

    # Rational quadratic arc on bottom
    arc_v = b.add(45, vertices=[1.0, 0.0, 0.0, 1.0, w, w, 0.0, w, 0.0, 1.0, 0.0, 1.0], varlen=12)
    arc_m = b.add(127, mult=[3, 3], varlen=2)
    arc_k = b.add(128, knots=[0.0, 1.0], varlen=2)
    arc_n = b.add(
        136,
        degree=2, n_vertices=3, vertex_dim=4, n_knots=2,
        periodic=False, closed=False, rational=True,
        bspline_vertices=arc_v, knot_mult=arc_m, knots=arc_k,
    )
    arc_b = b.add(134, nurbs=arc_n, sense="+", owner=body_idx)

    e_bot = b.add(16, curve=arc_b, owner=body_idx)
    e_r = b.add(16, curve=b.line((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx)
    e_top = b.add(16, curve=b.circle((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), 1.0, owner=body_idx), owner=body_idx)
    e_l = b.add(16, curve=b.line((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), owner=body_idx), owner=body_idx)

    edge_fins: dict[int, list[tuple[int, str]]] = {}
    f1, l1 = b.alloc(), b.alloc()
    fin0 = _wire_halfedges_in_loop(b, l1, [(e_bot, v0, "+"), (e_r, v1, "+"), (e_top, v2, "-"), (e_l, v3, "-")], edge_fins)
    b.add(15, idx=l1, face=f1, halfedge=fin0)
    b.add(14, idx=f1, shell=shell_idx, front_shell=shell_idx, surface=bsurf, loop=l1, sense="+")

    # Second sheet face: triangle (0,0,0)->(1,0,0)->(1,1,0) using 2D p-curve on bottom edge
    vp0 = b.vertex(b.point(0.0, 0.0, 0.0, owner=body_idx), owner=body_idx)
    vp1 = b.vertex(b.point(1.0, 0.0, 0.0, owner=body_idx), owner=body_idx)
    vp2 = b.vertex(b.point(1.0, 1.0, 0.0, owner=body_idx), owner=body_idx)
    ep0 = b.add(16, curve=sp2, owner=body_idx)
    ep1 = b.add(16, curve=b.line((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), owner=body_idx), owner=body_idx)
    ep2 = b.add(16, curve=b.line((1.0, 1.0, 0.0), (-1.0, -1.0, 0.0), owner=body_idx), owner=body_idx)
    f2, l2 = b.alloc(), b.alloc()
    fin_p = _wire_halfedges_in_loop(b, l2, [(ep0, vp0, "+"), (ep1, vp1, "+"), (ep2, vp2, "+")], edge_fins)
    b.add(15, idx=l2, face=f2, halfedge=fin_p)
    b.add(14, idx=f2, shell=shell_idx, front_shell=shell_idx, surface=pl, loop=l2, sense="+")

    return b.build_solid(body_idx=body_idx, shell_idx=shell_idx, region_idx=region_idx, body_type=2)


def make_two_boxes() -> ParseResult:
    """Build two disjoint solid boxes sharing one XT stream (multi-body test)."""
    box_a = make_box(1.0, 1.0, 1.0)
    box_b = make_box(2.0, 1.0, 1.0)
    offset = 1000
    shifted_nodes: list[XtNode] = []
    for n in box_b.nodes:
        new_fields: list[tuple[str, Any]] = []
        snode = builtin_schema()[n.type]
        fmeta = {f["name"]: f for f in effective(snode)}
        for k, v in n.fields:
            meta = fmeta.get(k)
            if meta and meta["code"] == "p":
                nv = [x + offset if isinstance(x, int) and x > 0 else x for x in v]
                new_fields.append((k, nv))
            elif k == "pvec" and n.type in (29, 30, 50):
                # Shift box_b by +3.0 along X
                pts = []
                for p in v:
                    pts.append([p[0] + 3.0, p[1], p[2]])
                new_fields.append((k, pts))
            else:
                new_fields.append((k, v))
        shifted_nodes.append(
            XtNode(type=n.type, name=n.name, index=n.index + offset, fields=new_fields, varlen=n.varlen)
        )
    return ParseResult(header=box_a.header, nodes=box_a.nodes + shifted_nodes, remaining_bytes=0)


def make_ellipse_and_intersection_sheets() -> ParseResult:
    """Build sheet faces exercising ELLIPSE(32), INTERSECTION(38)+CHART(40), None x_axis, and halfedge curve fallback."""
    b = _XtBrepBuilder()
    body_idx, shell_idx, region_idx = b.alloc(), b.alloc(), b.alloc()

    # Planar quarter-ellipse sheet: (2,0,0) -> (0,1,0) -> (0,0,0) -> (2,0,0)
    v0 = b.vertex(b.point(2.0, 0.0, 0.0, owner=body_idx), owner=body_idx)
    v1 = b.vertex(b.point(0.0, 1.0, 0.0, owner=body_idx), owner=body_idx)
    v2 = b.vertex(b.point(0.0, 0.0, 0.0, owner=body_idx), owner=body_idx)
    pl = b.plane((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), owner=body_idx)
    # Include a None inside SPUN_SURF x_axis in nodes (not referenced by valid face, or tested separately)
    b.add(68, base=[0.0, 0.0, 0.0], axis=[0.0, 0.0, 1.0], x_axis=[None, None, None], profile=None, sense="+")

    e_ell = b.add(16, curve=b.ellipse((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), 2.0, 1.0, owner=body_idx))
    e_y = b.add(16, curve=b.line((0.0, 1.0, 0.0), (0.0, -1.0, 0.0), owner=body_idx))
    e_x = b.add(16, curve=b.line((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), owner=body_idx))

    edge_fins: dict[int, list[tuple[int, str]]] = {}
    f1, l1 = b.alloc(), b.alloc()
    fin0 = _wire_halfedges_in_loop(b, l1, [(e_ell, v0, "+"), (e_y, v1, "+"), (e_x, v2, "+")], edge_fins)
    b.add(15, idx=l1, face=f1, halfedge=fin0)
    b.add(14, idx=f1, shell=shell_idx, front_shell=shell_idx, surface=pl, loop=l1, sense="+")

    # Second planar triangle whose hypotenuse uses INTERSECTION(38)+CHART(40) via HALFEDGE.curve fallback (EDGE.curve=0)
    va = b.vertex(b.point(0.0, 0.0, 0.5, owner=body_idx), owner=body_idx)
    vb = b.vertex(b.point(1.0, 0.0, 0.5, owner=body_idx), owner=body_idx)
    vc = b.vertex(b.point(0.0, 1.0, 0.5, owner=body_idx), owner=body_idx)
    pl2 = b.plane((0.0, 0.0, 0.5), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), owner=body_idx)

    chart = b.add(40, hvec=[[1.0, 0.0, 0.5], [0.5, 0.5, 0.5], [0.0, 1.0, 0.5]], varlen=3)
    icurve = b.add(38, surface=[pl2, pl2], chart=chart, sense="+")
    ea = b.add(16, curve=b.line((0.0, 0.0, 0.5), (1.0, 0.0, 0.5), owner=body_idx))
    eb = b.add(16, curve=0, owner=body_idx)  # Force fallback to HALFEDGE.curve
    ec = b.add(16, curve=b.line((0.0, 1.0, 0.5), (0.0, -1.0, 0.0), owner=body_idx))
    f2, l2 = b.alloc(), b.alloc()
    fin2 = _wire_halfedges_in_loop(b, l2, [(ea, va, "+"), (eb, vb, "+"), (ec, vc, "+")], edge_fins)
    # Set HALFEDGE curve on eb's fin & hook EDGE.halfedge
    fin_eb = edge_fins[eb][0][0]
    for node in b._nodes:
        if node.index == fin_eb:
            for i, (k, _) in enumerate(node.fields):
                if k == "curve":
                    node.fields[i] = ("curve", [icurve])
        elif node.index == eb:
            for i, (k, _) in enumerate(node.fields):
                if k == "halfedge":
                    node.fields[i] = ("halfedge", [fin_eb])
    b.add(15, idx=l2, face=f2, halfedge=fin2)
    b.add(14, idx=f2, shell=shell_idx, front_shell=shell_idx, surface=pl2, loop=l2, sense="+")

    return b.build_solid(body_idx=body_idx, shell_idx=shell_idx, region_idx=region_idx, body_type=2)


def _attach_attr(
    nodes: list[XtNode],
    alloc_fn,
    owner_idx: int,
    attr_name: str,
    val_type: int,
    val_field: str,
    val_data: list[Any],
    field_type_code: int,
) -> int:
    schema = builtin_schema()

    def _mk(ntype: int, **vals: Any) -> int:
        idx = alloc_fn()
        varlen = vals.pop("_varlen", None)
        sn = schema[ntype]
        fields: list[tuple[str, Any]] = []
        for f in effective(sn):
            fn = f["name"]
            if fn in vals:
                v = vals[fn]
                if f["code"] == "c" and isinstance(v, str):
                    v = [ord(ch) for ch in v]
                elif not isinstance(v, list):
                    v = [v]
            else:
                if fn == "node_id":
                    v = [idx]
                elif f["elements"] == 1:
                    v = []
                elif f["elements"] > 1:
                    v = [0] * f["elements"]
                elif f["code"] == "p":
                    v = [None]
                elif f["code"] in ("u", "n", "w", "d", "t"):
                    v = [0]
                elif f["code"] == "f":
                    v = [0.0]
                elif f["code"] == "l":
                    v = [False]
                elif f["code"] == "c":
                    v = [ord("+")]
                else:
                    v = [0]
            fields.append((fn, v))
        nodes.append(XtNode(type=ntype, name=sn["name"], index=idx, fields=fields, varlen=varlen))
        return idx

    name_bytes = [ord(c) for c in attr_name]
    id_idx = _mk(79, string=name_bytes, _varlen=len(name_bytes))
    def_idx = _mk(
        80,
        identifier=id_idx,
        type_id=1001,
        legal_owners=[True] * 14,
        fields=[field_type_code],
        _varlen=1,
    )
    val_idx = _mk(val_type, **{val_field: val_data, "_varlen": len(val_data)})
    return _mk(81, definition=def_idx, owner=owner_idx, fields=[val_idx], _varlen=1)


def make_colored_box() -> ParseResult:
    """Build a 20x30x40 mm (0.02x0.03x0.04 m) box with SDL/TYSA_NAME and SDL/TYSA_COLOUR."""
    base = make_box(0.02, 0.03, 0.04)
    nodes = list(base.nodes)
    nxt = [max(n.index for n in nodes) + 1]

    def alloc() -> int:
        v = nxt[0]
        nxt[0] += 1
        return v

    body_idx = next(n.index for n in nodes if n.type == 12)
    face_idx = next(n.index for n in nodes if n.type == 14)

    _attach_attr(nodes, alloc, body_idx, "SDL/TYSA_NAME", 84, "values", [ord(c) for c in "RedBlock"], 1)
    _attach_attr(nodes, alloc, body_idx, "SDL/TYSA_COLOUR", 83, "values", [0.85, 0.15, 0.15], 2)
    _attach_attr(nodes, alloc, face_idx, "SDL/TYSA_COLOUR", 83, "values", [0.1, 0.4, 0.9], 2)

    nodes.sort(key=lambda n: n.index)
    return ParseResult(header=base.header, nodes=nodes, remaining_bytes=0)


def make_assembly_two_parts() -> ParseResult:
    """Build an ASSEMBLY(10) with two INSTANCE(11) placements of a 10x10x10 mm box."""
    base = make_box(0.01, 0.01, 0.01)
    b = _XtBrepBuilder()
    b._nodes = list(base.nodes)
    b._next_idx = max(n.index for n in b._nodes) + 1
    nodes = b._nodes

    def alloc() -> int:
        return b.alloc()

    body_idx = next(n.index for n in nodes if n.type == 12)
    asm_idx = b.alloc()
    inst1_idx = b.alloc()
    inst2_idx = b.alloc()

    # Transform 1: identity at (0, 0, 0)
    tf1_idx = b.add(
        100,
        owner=inst1_idx,
        rotation_matrix=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        translation_vector=[0.0, 0.0, 0.0],
        scale=1.0,
        flag=1,
        perspective_vector=[0.0, 0.0, 0.0],
    )
    # Transform 2: rotate 90 deg about Z, translate +0.05 m along X (50 mm)
    tf2_idx = b.add(
        100,
        owner=inst2_idx,
        rotation_matrix=[0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        translation_vector=[0.05, 0.0, 0.0],
        scale=1.0,
        flag=1,
        perspective_vector=[0.0, 0.0, 0.0],
    )

    b.add(11, idx=inst1_idx, type=1, part=body_idx, transform=tf1_idx, assembly=asm_idx, next_in_part=inst2_idx)
    b.add(11, idx=inst2_idx, type=1, part=body_idx, transform=tf2_idx, assembly=asm_idx, prev_in_part=inst1_idx)
    b.add(10, idx=asm_idx, highest_node_id=b._next_idx + 10, state=1, type=1, sub_instance=inst1_idx)

    _attach_attr(nodes, alloc, asm_idx, "SDL/TYSA_NAME", 84, "values", [ord(c) for c in "TwoBlockAssembly"], 1)
    _attach_attr(nodes, alloc, body_idx, "SDL/TYSA_NAME", 84, "values", [ord(c) for c in "UnitBlock"], 1)
    _attach_attr(nodes, alloc, inst1_idx, "SDL/TYSA_NAME", 84, "values", [ord(c) for c in "Block_A"], 1)
    _attach_attr(nodes, alloc, inst2_idx, "SDL/TYSA_NAME", 84, "values", [ord(c) for c in "Block_B"], 1)

    nodes.sort(key=lambda n: n.index)
    return ParseResult(header=base.header, nodes=nodes, remaining_bytes=0)


@pytest.fixture
def box_model() -> ParseResult:
    return make_box(1.0, 2.0, 3.0)
