"""High-level B-rep model extraction from parsed Parasolid XT nodes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

from .xb import ParseResult, XtHeader, XtNode


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _safe_vec3(
    raw: Any,
    default: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    if not raw:
        return default
    if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], (list, tuple)):
        raw = raw[0]
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return default
    x = _safe_float(raw[0], default[0])
    y = _safe_float(raw[1], default[1])
    z = _safe_float(raw[2], default[2])
    return (x, y, z)


def _decode_sense(val: Any) -> str:
    if isinstance(val, str):
        return "-" if val == "-" else "+"
    if isinstance(val, int):
        return "-" if val in (0, ord("-")) else "+"
    return "+"


def _prune_knots(knots: list[float], mults: list[int]) -> tuple[list[float], list[int]]:
    if not mults or len(mults) != len(knots):
        return list(knots), list(mults)
    out_k: list[float] = []
    out_m: list[int] = []
    for k, m in zip(knots, mults):
        if m and m > 0:
            out_k.append(float(k))
            out_m.append(int(m))
    return out_k, out_m


def _node_map(source: ParseResult | list[XtNode] | dict[int, XtNode]) -> tuple[XtHeader | None, dict[int, XtNode]]:
    if isinstance(source, ParseResult):
        return source.header, source.by_index()
    if isinstance(source, dict):
        return None, source
    return None, {n.index: n for n in source}


@dataclass
class NurbsCurve:
    """Extracted NURBS curve (3D space curve or 2D surface p-curve)."""

    degree: int
    poles: list[tuple[float, float, float]]
    knots: list[float]
    multiplicities: list[int]
    weights: list[float] | None = None
    periodic: bool = False
    closed: bool = False
    rational: bool = False
    vertex_dim: int = 3
    is_pcurve: bool = False
    poles_2d: list[tuple[float, float]] = field(default_factory=list)
    host_surface_index: int | None = None


@dataclass
class NurbsSurface:
    """Extracted B-spline / NURBS surface."""

    u_degree: int
    v_degree: int
    u_knots: list[float]
    u_mults: list[int]
    v_knots: list[float]
    v_mults: list[int]
    poles: list[list[tuple[float, float, float]]]
    weights: list[list[float]] | None = None
    u_periodic: bool = False
    v_periodic: bool = False
    u_closed: bool = False
    v_closed: bool = False
    rational: bool = False


@dataclass
class Attribute:
    """Decoded Parasolid attribute attached to a topological/geometric node."""

    index: int
    name: str
    owner_index: int | None
    values: list[Any] = field(default_factory=list)


@dataclass
class Appearance:
    """Decoded standard Parasolid SDL/TYSA appearance & metadata attributes."""

    color_rgb: tuple[float, float, float] | None = None
    transparency: float | None = None
    name: str | None = None
    layer: int | None = None
    density: float | None = None

    def apply_attribute(self, attr: Attribute) -> None:
        upper = attr.name.upper()
        if upper.endswith("TYSA_COLOUR") or upper.endswith("TYSA_COLOR"):
            reals: list[float] = []
            for v in attr.values:
                if isinstance(v, (list, tuple)):
                    for item in v:
                        if isinstance(item, (int, float)):
                            reals.append(float(item))
            if len(reals) >= 3:
                self.color_rgb = (
                    max(0.0, min(1.0, reals[0])),
                    max(0.0, min(1.0, reals[1])),
                    max(0.0, min(1.0, reals[2])),
                )
        elif upper.endswith("TYSA_NAME"):
            for v in attr.values:
                if isinstance(v, str) and v.strip():
                    self.name = v.strip()
                    break
        elif upper.endswith("TYSA_TRANSPARENCY") or upper.endswith("TYSA_TRANSLUCENCY"):
            for v in attr.values:
                if isinstance(v, (list, tuple)) and v and isinstance(v[0], (int, float)):
                    val = float(v[0])
                    if math.isfinite(val):
                        self.transparency = max(0.0, min(1.0, val))
                        break
        elif upper.endswith("TYSA_DENSITY"):
            for v in attr.values:
                if isinstance(v, (list, tuple)) and v and isinstance(v[0], (int, float)):
                    val = float(v[0])
                    if math.isfinite(val) and val > 0.0:
                        self.density = val
                        break
        elif upper.endswith("TYSA_LAYER"):
            for v in attr.values:
                if isinstance(v, (list, tuple)) and v and isinstance(v[0], int):
                    self.layer = int(v[0])
                    break

    def inherit_from(self, parent: Appearance) -> Appearance:
        return Appearance(
            color_rgb=self.color_rgb if self.color_rgb is not None else parent.color_rgb,
            transparency=self.transparency if self.transparency is not None else parent.transparency,
            name=self.name if self.name is not None else parent.name,
            layer=self.layer if self.layer is not None else parent.layer,
            density=self.density if self.density is not None else parent.density,
        )


@dataclass
class AssemblyInfo:
    """Topological assembly node grouping sub-assembly / body instances."""

    index: int
    node_id: int
    name: str | None = None
    appearance: Appearance = field(default_factory=Appearance)
    instance_indices: list[int] = field(default_factory=list)


@dataclass
class Vertex:
    """Topological vertex with 3D coordinates."""

    index: int
    node_id: int
    point: tuple[float, float, float]
    tolerance: float = 0.0


@dataclass
class Edge:
    """Topological edge with its supporting 3D or p-curve geometry."""

    index: int
    node_id: int
    curve_type: str
    sense: str = "+"
    tolerance: float = 0.0
    curve_index: int | None = None
    curve: dict[str, Any] = field(default_factory=dict)


@dataclass
class Coedge:
    """Oriented edge use (HALFEDGE / fin) inside a face loop."""

    edge_index: int
    sense: str
    start_vertex: int | None
    end_vertex: int | None
    fin_index: int


@dataclass
class Loop:
    """Closed cycle of coedges bounding a face."""

    index: int
    coedges: list[Coedge] = field(default_factory=list)


@dataclass
class Face:
    """Topological face with surface geometry, combined orientation, and loops."""

    index: int
    node_id: int
    shell_index: int | None
    sense: str
    face_sense: str
    surface_sense: str
    surface_type: str
    surface_index: int | None = None
    surface: dict[str, Any] = field(default_factory=dict)
    loops: list[Loop] = field(default_factory=list)
    tolerance: float = 0.0
    attributes: list[Attribute] = field(default_factory=list)
    appearance: Appearance = field(default_factory=Appearance)


@dataclass
class BrepModel:
    """Structured boundary-representation model extracted from XT records."""

    header: XtHeader | None
    bodies: list[dict[str, Any]]
    vertices: dict[int, Vertex]
    edges: dict[int, Edge]
    faces: dict[int, Face]
    surfaces: dict[int, dict[str, Any]]
    curves: dict[int, dict[str, Any]]
    attributes: list[Attribute] = field(default_factory=list)
    instances: list[dict[str, Any]] = field(default_factory=list)
    assemblies: list[AssemblyInfo] = field(default_factory=list)

    def bbox(self) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """Compute axis-aligned bounding box from vertex points (or NURBS surface poles)."""
        pts: list[tuple[float, float, float]] = [v.point for v in self.vertices.values()]
        if not pts:
            for s in self.surfaces.values():
                nurbs = s.get("nurbs")
                if isinstance(nurbs, NurbsSurface):
                    for row in nurbs.poles:
                        pts.extend(row)
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def euler_characteristic(self) -> int:
        """Return V - E + F across extracted topology."""
        return len(self.vertices) - len(self.edges) + len(self.faces)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dictionary representation suitable for JSON serialization."""
        return {
            "header": asdict(self.header) if self.header is not None else None,
            "bodies": list(self.bodies),
            "counts": {
                "bodies": len(self.bodies),
                "assemblies": len(self.assemblies),
                "instances": len(self.instances),
                "faces": len(self.faces),
                "edges": len(self.edges),
                "vertices": len(self.vertices),
                "surfaces": len(self.surfaces),
                "curves": len(self.curves),
                "attributes": len(self.attributes),
            },
            "bbox": self.bbox(),
            "euler": self.euler_characteristic(),
            "assemblies": [asdict(a) for a in self.assemblies],
            "instances": list(self.instances),
            "faces": [asdict(f) for f in self.faces.values()],
            "edges": [asdict(e) for e in self.edges.values()],
            "vertices": [asdict(v) for v in self.vertices.values()],
            "attributes": [asdict(a) for a in self.attributes],
        }


def _extract_nurbs_surface(n: XtNode, nodes: dict[int, XtNode]) -> dict[str, Any]:
    nu = int(n.get("n_u_vertices") or 0)
    nv = int(n.get("n_v_vertices") or 0)
    vdim = int(n.get("vertex_dim") or 3)
    rat = bool(n.get("rational"))
    v_node = nodes.get(n.get("bspline_vertices"))
    raw_v = [float(x) for x in v_node.get_list("vertices")] if v_node else []
    uk_node = nodes.get(n.get("u_knots"))
    vk_node = nodes.get(n.get("v_knots"))
    um_node = nodes.get(n.get("u_knot_mult"))
    vm_node = nodes.get(n.get("v_knot_mult"))
    uk_raw = [float(x) for x in uk_node.get_list("knots")] if uk_node else []
    vk_raw = [float(x) for x in vk_node.get_list("knots")] if vk_node else []
    um_raw = [int(x) for x in um_node.get_list("mult")] if um_node else []
    vm_raw = [int(x) for x in vm_node.get_list("mult")] if vm_node else []
    u_knots, u_mults = _prune_knots(uk_raw, um_raw)
    v_knots, v_mults = _prune_knots(vk_raw, vm_raw)

    poles: list[list[tuple[float, float, float]]] = []
    weights: list[list[float]] | None = [] if rat else None
    for iu in range(nu):
        row_p: list[tuple[float, float, float]] = []
        row_w: list[float] = []
        for iv in range(nv):
            idx = (iu * nv + iv) * vdim
            if idx + 2 >= len(raw_v):
                row_p.append((0.0, 0.0, 0.0))
                if weights is not None:
                    row_w.append(1.0)
                continue
            if rat and vdim >= 4 and idx + 3 < len(raw_v):
                w = raw_v[idx + 3] or 1.0
                row_p.append((raw_v[idx] / w, raw_v[idx + 1] / w, raw_v[idx + 2] / w))
                row_w.append(w)
            else:
                row_p.append((raw_v[idx], raw_v[idx + 1], raw_v[idx + 2]))
                if weights is not None:
                    row_w.append(1.0)
        poles.append(row_p)
        if weights is not None:
            weights.append(row_w)

    ns = NurbsSurface(
        u_degree=int(n.get("u_degree") or 1),
        v_degree=int(n.get("v_degree") or 1),
        u_knots=u_knots,
        u_mults=u_mults,
        v_knots=v_knots,
        v_mults=v_mults,
        poles=poles,
        weights=weights,
        u_periodic=bool(n.get("u_periodic")),
        v_periodic=bool(n.get("v_periodic")),
        u_closed=bool(n.get("u_closed")),
        v_closed=bool(n.get("v_closed")),
        rational=rat,
    )
    return {"type": "nurbs", "sense": "+", "nurbs": ns}


def _extract_surface(
    s_idx: int | None,
    nodes: dict[int, XtNode],
    cache: dict[int, dict[str, Any]],
    seen: set[int] | None = None,
) -> dict[str, Any]:
    if s_idx is None or s_idx not in nodes:
        return {"type": "unknown", "sense": "+"}
    if s_idx in cache:
        return cache[s_idx]
    if seen is None:
        seen = set()
    if s_idx in seen:
        return {"type": "unknown", "sense": "+"}
    seen.add(s_idx)

    s = nodes[s_idx]
    t = s.type
    sense = _decode_sense(s.get("sense"))
    out: dict[str, Any]
    if t == 50:  # PLANE
        out = {
            "type": "plane",
            "sense": sense,
            "origin": _safe_vec3(s.get("pvec"), (0.0, 0.0, 0.0)),
            "normal": _safe_vec3(s.get("normal"), (0.0, 0.0, 1.0)),
            "x_axis": _safe_vec3(s.get("x_axis"), (1.0, 0.0, 0.0)),
        }
    elif t == 51:  # CYLINDER
        out = {
            "type": "cylinder",
            "sense": sense,
            "origin": _safe_vec3(s.get("pvec"), (0.0, 0.0, 0.0)),
            "axis": _safe_vec3(s.get("axis"), (0.0, 0.0, 1.0)),
            "x_axis": _safe_vec3(s.get("x_axis"), (1.0, 0.0, 0.0)),
            "radius": _safe_float(s.get("radius"), 0.0),
        }
    elif t == 52:  # CONE
        out = {
            "type": "cone",
            "sense": sense,
            "origin": _safe_vec3(s.get("pvec"), (0.0, 0.0, 0.0)),
            "axis": _safe_vec3(s.get("axis"), (0.0, 0.0, 1.0)),
            "x_axis": _safe_vec3(s.get("x_axis"), (1.0, 0.0, 0.0)),
            "radius": _safe_float(s.get("radius"), 0.0),
            "sin_half_angle": _safe_float(s.get("sin_half_angle"), 0.0),
            "cos_half_angle": _safe_float(s.get("cos_half_angle"), 1.0),
        }
    elif t == 53:  # SPHERE
        out = {
            "type": "sphere",
            "sense": sense,
            "centre": _safe_vec3(s.get("centre"), (0.0, 0.0, 0.0)),
            "axis": _safe_vec3(s.get("axis"), (0.0, 0.0, 1.0)),
            "x_axis": _safe_vec3(s.get("x_axis"), (1.0, 0.0, 0.0)),
            "radius": _safe_float(s.get("radius"), 0.0),
        }
    elif t == 54:  # TORUS
        out = {
            "type": "torus",
            "sense": sense,
            "centre": _safe_vec3(s.get("centre"), (0.0, 0.0, 0.0)),
            "axis": _safe_vec3(s.get("axis"), (0.0, 0.0, 1.0)),
            "x_axis": _safe_vec3(s.get("x_axis"), (1.0, 0.0, 0.0)),
            "major_radius": _safe_float(s.get("major_radius"), 0.0),
            "minor_radius": _safe_float(s.get("minor_radius"), 0.0),
        }
    elif t == 124:  # B_SURFACE
        nurbs_idx = s.get("nurbs")
        if nurbs_idx in nodes and nodes[nurbs_idx].type == 126:
            out = _extract_nurbs_surface(nodes[nurbs_idx], nodes)
            out["sense"] = sense
        else:
            out = {"type": "b_surface", "sense": sense}
    elif t == 126:
        out = _extract_nurbs_surface(s, nodes)
    elif t == 67:  # SWEPT_SURF
        sec_idx = s.get("section")
        out = {
            "type": "swept",
            "sense": sense,
            "direction": _safe_vec3(s.get("sweep"), (0.0, 0.0, 1.0)),
            "scale": _safe_float(s.get("scale"), 1.0),
            "profile_index": sec_idx,
            "profile": _extract_curve(sec_idx, nodes, {}, cache),
        }
    elif t == 68:  # SPUN_SURF
        prof_idx = s.get("profile")
        out = {
            "type": "spun",
            "sense": sense,
            "base": _safe_vec3(s.get("base"), (0.0, 0.0, 0.0)),
            "axis": _safe_vec3(s.get("axis"), (0.0, 0.0, 1.0)),
            "x_axis": _safe_vec3(s.get("x_axis"), (1.0, 0.0, 0.0)),
            "start": _safe_vec3(s.get("start"), (0.0, 0.0, 0.0)),
            "end": _safe_vec3(s.get("end"), (0.0, 0.0, 0.0)),
            "start_param": s.get("start_param"),
            "end_param": s.get("end_param"),
            "profile_index": prof_idx,
            "profile": _extract_curve(prof_idx, nodes, {}, cache),
        }
    elif t == 60:  # OFFSET_SURF
        base_idx = s.get("surface")
        out = {
            "type": "offset",
            "sense": sense,
            "offset": _safe_float(s.get("offset"), 0.0),
            "base_surface_index": base_idx,
            "base_surface": _extract_surface(base_idx, nodes, cache, seen),
        }
    elif t == 56:  # BLENDED_EDGE
        out = {"type": "blended_edge", "sense": sense, "blend_type": s.get("blend_type")}
    else:
        out = {"type": s.name.lower(), "sense": sense, "xt_type": t}

    cache[s_idx] = out
    return out


def _extract_nurbs_curve(
    n: XtNode,
    nodes: dict[int, XtNode],
    host_surface_index: int | None = None,
) -> NurbsCurve:
    nc = int(n.get("n_vertices") or 0)
    vdim = int(n.get("vertex_dim") or 3)
    rat = bool(n.get("rational"))
    v_node = nodes.get(n.get("bspline_vertices"))
    raw_v = [float(x) for x in v_node.get_list("vertices")] if v_node else []
    k_node = nodes.get(n.get("knots"))
    m_node = nodes.get(n.get("knot_mult"))
    k_raw = [float(x) for x in k_node.get_list("knots")] if k_node else []
    m_raw = [int(x) for x in m_node.get_list("mult")] if m_node else []
    knots, mults = _prune_knots(k_raw, m_raw)

    is_pcurve = vdim == 2 or (vdim == 3 and rat and host_surface_index is not None)
    poles: list[tuple[float, float, float]] = []
    poles_2d: list[tuple[float, float]] = []
    weights: list[float] | None = [] if rat else None

    for i in range(nc):
        base = i * vdim
        if is_pcurve:
            if base + 1 >= len(raw_v):
                poles_2d.append((0.0, 0.0))
                if weights is not None:
                    weights.append(1.0)
                continue
            if rat and vdim >= 3 and base + 2 < len(raw_v):
                w = raw_v[base + 2] or 1.0
                poles_2d.append((raw_v[base] / w, raw_v[base + 1] / w))
                if weights is not None:
                    weights.append(w)
            else:
                poles_2d.append((raw_v[base], raw_v[base + 1]))
                if weights is not None:
                    weights.append(1.0)
        else:
            if base + 2 >= len(raw_v):
                poles.append((0.0, 0.0, 0.0))
                if weights is not None:
                    weights.append(1.0)
                continue
            if rat and vdim >= 4 and base + 3 < len(raw_v):
                w = raw_v[base + 3] or 1.0
                poles.append((raw_v[base] / w, raw_v[base + 1] / w, raw_v[base + 2] / w))
                if weights is not None:
                    weights.append(w)
            else:
                poles.append((raw_v[base], raw_v[base + 1], raw_v[base + 2]))
                if weights is not None:
                    weights.append(1.0)

    return NurbsCurve(
        degree=int(n.get("degree") or 1),
        poles=poles,
        knots=knots,
        multiplicities=mults,
        weights=weights,
        periodic=bool(n.get("periodic")),
        closed=bool(n.get("closed")),
        rational=rat,
        vertex_dim=vdim,
        is_pcurve=is_pcurve,
        poles_2d=poles_2d,
        host_surface_index=host_surface_index,
    )


def _extract_curve(
    c_idx: int | None,
    nodes: dict[int, XtNode],
    curve_cache: dict[int, dict[str, Any]],
    surface_cache: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if c_idx is None or c_idx not in nodes:
        return {"type": "unknown", "sense": "+"}
    if c_idx in curve_cache:
        return curve_cache[c_idx]

    trim_info: dict[str, Any] = {}
    sp_surface_index: int | None = None
    cur_idx: int | None = c_idx
    seen: set[int] = set()

    while cur_idx is not None and cur_idx in nodes and cur_idx not in seen:
        seen.add(cur_idx)
        c = nodes[cur_idx]
        if c.type == 133:  # TRIMMED_CURVE
            trim_info = {
                "parm_1": c.get("parm_1"),
                "parm_2": c.get("parm_2"),
                "point_1": c.get("point_1"),
                "point_2": c.get("point_2"),
            }
            cur_idx = c.get("basis_curve")
            continue
        if c.type == 137:  # SP_CURVE
            if sp_surface_index is None:
                sp_surface_index = c.get("surface")
            nxt = c.get("b_curve")
            if nxt is None:
                nxt = c.get("original")
            cur_idx = nxt
            continue
        if c.type == 134:  # B_CURVE
            cur_idx = c.get("nurbs")
            continue
        break

    if cur_idx is None or cur_idx not in nodes:
        out: dict[str, Any] = {"type": "unknown", "sense": "+"}
        curve_cache[c_idx] = out
        return out

    c = nodes[cur_idx]
    t = c.type
    sense = _decode_sense(c.get("sense"))
    if t == 30:  # LINE
        out = {
            "type": "line",
            "sense": sense,
            "origin": _safe_vec3(c.get("pvec"), (0.0, 0.0, 0.0)),
            "dir": _safe_vec3(c.get("direction"), (1.0, 0.0, 0.0)),
        }
    elif t == 31:  # CIRCLE
        out = {
            "type": "circle",
            "sense": sense,
            "centre": _safe_vec3(c.get("centre"), (0.0, 0.0, 0.0)),
            "normal": _safe_vec3(c.get("normal"), (0.0, 0.0, 1.0)),
            "x_axis": _safe_vec3(c.get("x_axis"), (1.0, 0.0, 0.0)),
            "radius": _safe_float(c.get("radius"), 0.0),
        }
    elif t == 32:  # ELLIPSE
        out = {
            "type": "ellipse",
            "sense": sense,
            "centre": _safe_vec3(c.get("centre"), (0.0, 0.0, 0.0)),
            "normal": _safe_vec3(c.get("normal"), (0.0, 0.0, 1.0)),
            "x_axis": _safe_vec3(c.get("x_axis"), (1.0, 0.0, 0.0)),
            "major_radius": _safe_float(c.get("major_radius"), 0.0),
            "minor_radius": _safe_float(c.get("minor_radius"), 0.0),
        }
    elif t == 136:  # NURBS_CURVE
        nurbs = _extract_nurbs_curve(c, nodes, host_surface_index=sp_surface_index)
        out = {"type": "nurbs", "sense": sense, "nurbs": nurbs}
    elif t == 38:  # INTERSECTION
        surfs = c.get_list("surface")
        pts: list[tuple[float, float, float]] = []

        def _append_hvec_node(nid: int | None) -> None:
            if not nid or nid not in nodes:
                return
            for hv in nodes[nid].get_list("hvec"):
                pt = _safe_vec3(hv)
                if not pts or (
                    (pt[0] - pts[-1][0]) ** 2
                    + (pt[1] - pts[-1][1]) ** 2
                    + (pt[2] - pts[-1][2]) ** 2
                ) > 1e-24:
                    pts.append(pt)

        _append_hvec_node(c.get("start"))
        _append_hvec_node(c.get("chart"))
        _append_hvec_node(c.get("end"))
        out = {
            "type": "intersection",
            "sense": sense,
            "surface_indices": surfs,
            "chart": c.get("chart"),
            "points": pts,
        }
    else:
        out = {"type": c.name.lower(), "sense": sense, "xt_type": t}

    if trim_info:
        out["trim"] = trim_info
    if sp_surface_index is not None:
        out["sp_surface_index"] = sp_surface_index
        out["sp_surface"] = _extract_surface(sp_surface_index, nodes, surface_cache)

    curve_cache[c_idx] = out
    return out


def _extract_attributes(
    nodes: dict[int, XtNode],
) -> tuple[list[Attribute], dict[int, list[Attribute]], dict[int, Appearance]]:
    owner_from_chain: dict[int, int] = {}
    for n in nodes.values():
        if n.type not in (10, 11, 12, 13, 14, 15, 16, 18, 29):
            continue
        cur = n.get("attributes_groups")
        seen: set[int] = set()
        while cur and cur in nodes and cur not in seen:
            seen.add(cur)
            owner_from_chain.setdefault(cur, n.index)
            cur = nodes[cur].get("next")

    all_attrs: list[Attribute] = []
    by_owner: dict[int, list[Attribute]] = {}
    app_by_owner: dict[int, Appearance] = {}
    for n in nodes.values():
        if n.type != 81:  # ATTRIBUTE
            continue
        def_name = "unknown"
        d_idx = n.get("definition")
        if d_idx in nodes:
            id_idx = nodes[d_idx].get("identifier")
            if id_idx in nodes:
                raw_chars = nodes[id_idx].get_list("string")
                def_name = (
                    bytes(int(x) & 0xFF for x in raw_chars)
                    .split(b"\x00", 1)[0]
                    .decode("ascii", "replace")
                )
        vals: list[Any] = []
        for f_idx in n.get_list("fields"):
            vn = nodes.get(f_idx)
            if vn is None:
                continue
            if vn.type in (84, 102):  # CHAR_VALUES / KEY
                raw_chars = vn.get_list("values") if vn.type == 84 else vn.get_list("string")
                vals.append(
                    bytes(int(x) & 0xFF for x in raw_chars)
                    .split(b"\x00", 1)[0]
                    .decode("utf-8", "replace")
                )
            elif vn.type == 98:  # UNICODE_VALUES
                raw_u = vn.get_list("values")
                chars = []
                for u in raw_u:
                    code = int(u) & 0xFFFF
                    if code == 0:
                        break
                    chars.append(chr(code))
                vals.append("".join(chars))
            else:
                vals.append(vn.get_list("values"))
        owner_idx = n.get("owner") or owner_from_chain.get(n.index)
        attr = Attribute(index=n.index, name=def_name, owner_index=owner_idx, values=vals)
        all_attrs.append(attr)
        if owner_idx is not None:
            by_owner.setdefault(owner_idx, []).append(attr)
            app_by_owner.setdefault(owner_idx, Appearance()).apply_attribute(attr)
    return all_attrs, by_owner, app_by_owner


def extract_brep(source: ParseResult | list[XtNode] | dict[int, XtNode]) -> BrepModel:
    """Extract a high-level B-rep topological and geometric model from XT nodes."""
    header, nodes = _node_map(source)
    all_attrs, attrs_by_owner, app_by_owner = _extract_attributes(nodes)

    vertices: dict[int, Vertex] = {}
    for n in nodes.values():
        if n.type == 18:  # VERTEX
            p_node = nodes.get(n.get("point"))
            pvec = _safe_vec3(p_node.get("pvec") if p_node else None, (0.0, 0.0, 0.0))
            vertices[n.index] = Vertex(
                index=n.index,
                node_id=int(n.get("node_id") or 0),
                point=pvec,
                tolerance=_safe_float(n.get("tolerance"), 0.0),
            )

    surfaces: dict[int, dict[str, Any]] = {}
    curves: dict[int, dict[str, Any]] = {}

    edges: dict[int, Edge] = {}
    for n in nodes.values():
        if n.type != 16:  # EDGE
            continue
        c_idx = n.get("curve")
        if not c_idx:
            fin_idx = n.get("halfedge")
            visited_f: set[int] = set()
            while fin_idx and fin_idx in nodes and fin_idx not in visited_f:
                visited_f.add(fin_idx)
                fn = nodes[fin_idx]
                c_idx = fn.get("curve")
                if c_idx:
                    break
                fin_idx = fn.get("other")
        c_dict = _extract_curve(c_idx, nodes, curves, surfaces)
        edges[n.index] = Edge(
            index=n.index,
            node_id=int(n.get("node_id") or 0),
            curve_type=str(c_dict.get("type", "unknown")),
            sense=str(c_dict.get("sense", "+")),
            tolerance=float(n.get("tolerance") or 0.0),
            curve_index=c_idx,
            curve=c_dict,
        )

    faces: dict[int, Face] = {}
    for n in nodes.values():
        if n.type != 14:  # FACE
            continue
        s_idx = n.get("surface")
        s_dict = _extract_surface(s_idx, nodes, surfaces)
        face_sense = _decode_sense(n.get("sense"))
        surf_sense = str(s_dict.get("sense", "+"))
        combined_sense = "+" if face_sense == surf_sense else "-"

        loops: list[Loop] = []
        l_idx = n.get("loop")
        seen_l: set[int] = set()
        while l_idx is not None and l_idx in nodes and l_idx not in seen_l:
            seen_l.add(l_idx)
            ln = nodes[l_idx]
            coedges: list[Coedge] = []
            start_fin = ln.get("halfedge")
            cur_fin = start_fin
            seen_f: set[int] = set()
            while cur_fin is not None and cur_fin in nodes and cur_fin not in seen_f:
                seen_f.add(cur_fin)
                fn = nodes[cur_fin]
                fwd_idx = fn.get("forward")
                bwd_idx = fn.get("backward")
                bwd = nodes.get(bwd_idx) if bwd_idx is not None else None
                e_idx = fn.get("edge")
                if e_idx is not None:
                    coedges.append(
                        Coedge(
                            edge_index=e_idx,
                            sense=_decode_sense(fn.get("sense")),
                            start_vertex=bwd.get("vertex") if bwd is not None else None,
                            end_vertex=fn.get("vertex"),
                            fin_index=cur_fin,
                        )
                    )
                cur_fin = fwd_idx
                if cur_fin == start_fin:
                    break
            loops.append(Loop(index=l_idx, coedges=coedges))
            l_idx = ln.get("next")

        faces[n.index] = Face(
            index=n.index,
            node_id=int(n.get("node_id") or 0),
            shell_index=n.get("shell"),
            sense=combined_sense,
            face_sense=face_sense,
            surface_sense=surf_sense,
            surface_type=str(s_dict.get("type", "unknown")),
            surface_index=s_idx,
            surface=s_dict,
            loops=loops,
            tolerance=float(n.get("tolerance") or 0.0),
            attributes=attrs_by_owner.get(n.index, []),
            appearance=app_by_owner.get(n.index, Appearance()),
        )

    bodies: list[dict[str, Any]] = []
    for n in nodes.values():
        if n.type != 12:  # BODY
            continue
        shell_indices: list[int] = []
        sh = n.get("shell")
        seen_s: set[int] = set()
        while sh is not None and sh in nodes and sh not in seen_s:
            seen_s.add(sh)
            shell_indices.append(sh)
            sh = nodes[sh].get("next")
        body_faces = [f.index for f in faces.values() if f.shell_index in seen_s]
        b_app = app_by_owner.get(n.index, Appearance())
        bodies.append(
            {
                "index": n.index,
                "node_id": int(n.get("node_id") or 0),
                "body_type": n.get("body_type"),
                "shell_indices": shell_indices,
                "face_count": len(body_faces),
                "name": b_app.name,
                "appearance": b_app,
            }
        )

    instances: list[dict[str, Any]] = []
    inst_by_asm: dict[int, list[int]] = {}
    for n in nodes.values():
        if n.type != 11:  # INSTANCE
            continue
        tf_idx = n.get("transform")
        tf_dict: dict[str, Any] | None = None
        if tf_idx and tf_idx in nodes:
            tn = nodes[tf_idx]
            rot = [_safe_float(x, 0.0) for x in tn.get_list("rotation_matrix")]
            sc = _safe_float(tn.get("scale"), 1.0)
            if abs(sc) < 1e-15:
                sc = 1.0
            tf_dict = {
                "rotation_matrix": rot,
                "translation": _safe_vec3(tn.get("translation_vector"), (0.0, 0.0, 0.0)),
                "scale": sc,
            }
        asm_idx = n.get("assembly")
        if asm_idx:
            inst_by_asm.setdefault(asm_idx, []).append(n.index)
        i_app = app_by_owner.get(n.index, Appearance())
        instances.append(
            {
                "index": n.index,
                "node_id": int(n.get("node_id") or 0),
                "type": n.get("type"),
                "part": n.get("part"),
                "assembly": asm_idx,
                "transform": tf_dict,
                "name": i_app.name,
                "appearance": i_app,
            }
        )

    assemblies: list[AssemblyInfo] = []
    for n in nodes.values():
        if n.type != 10:  # ASSEMBLY
            continue
        inst_ids: list[int] = []
        seen_i: set[int] = set()
        cur_i = n.get("sub_instance")
        while cur_i and cur_i in nodes and cur_i not in seen_i and nodes[cur_i].type == 11:
            seen_i.add(cur_i)
            inst_ids.append(cur_i)
            cur_i = nodes[cur_i].get("next_in_part")
        for extra_i in inst_by_asm.get(n.index, ()):
            if extra_i not in seen_i:
                seen_i.add(extra_i)
                inst_ids.append(extra_i)
        a_app = app_by_owner.get(n.index, Appearance())
        assemblies.append(
            AssemblyInfo(
                index=n.index,
                node_id=int(n.get("node_id") or 0),
                name=a_app.name,
                appearance=a_app,
                instance_indices=inst_ids,
            )
        )

    return BrepModel(
        header=header,
        bodies=bodies,
        vertices=vertices,
        edges=edges,
        faces=faces,
        surfaces=surfaces,
        curves=curves,
        attributes=all_attrs,
        instances=instances,
        assemblies=assemblies,
    )
