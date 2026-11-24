"""B-rep topology/geometry validation, summary statistics, and JSON export."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import json
import math
from typing import Any

from .model import BrepModel, NurbsCurve, NurbsSurface, extract_brep
from .xb import ParseResult


def _is_finite_pt(pt: tuple[float, ...]) -> bool:
    return all(math.isfinite(x) for x in pt)


@dataclass
class ValidationReport:
    """Result of running structural, topological, and geometric checks on an XT model."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _ensure_model(source: ParseResult | BrepModel) -> tuple[ParseResult | None, BrepModel]:
    if isinstance(source, ParseResult):
        return source, extract_brep(source)
    return None, source


def validate(source: ParseResult | BrepModel) -> ValidationReport:
    """Validate XT parse integrity, B-rep loop closure, edge pairing, and geometry sanity."""
    parsed, model = _ensure_model(source)
    errors: list[str] = []
    warnings: list[str] = []

    if parsed is not None:
        if parsed.remaining_bytes:
            warnings.append(f"trailing unread bytes after stream end: {parsed.remaining_bytes}")
        if not parsed.nodes:
            errors.append("no XT nodes decoded")

    if not model.faces and not model.edges and not model.vertices:
        errors.append("empty B-rep model (no faces, edges, or vertices)")

    for v in model.vertices.values():
        if not _is_finite_pt(v.point):
            errors.append(f"vertex {v.index}: non-finite coordinates {v.point}")

    for e in model.edges.values():
        if e.curve_type == "unknown":
            warnings.append(f"edge {e.index}: missing or unsupported curve")
        elif e.curve_type == "circle" and float(e.curve.get("radius", 0.0)) <= 0.0:
            errors.append(f"edge {e.index}: non-positive circle radius")
        elif e.curve_type == "ellipse":
            if float(e.curve.get("major_radius", 0.0)) <= 0.0 or float(e.curve.get("minor_radius", 0.0)) <= 0.0:
                errors.append(f"edge {e.index}: non-positive ellipse radius")
        elif e.curve_type == "nurbs":
            nc: NurbsCurve | None = e.curve.get("nurbs")
            if nc is not None:
                n_poles = len(nc.poles_2d) if nc.is_pcurve else len(nc.poles)
                knot_sum = sum(nc.multiplicities)
                if knot_sum and knot_sum != n_poles + nc.degree + 1:
                    warnings.append(
                        f"edge {e.index}: NURBS knot multiplicity sum {knot_sum} != poles+deg+1 ({n_poles + nc.degree + 1})"
                    )

    edge_use_counts: Counter[int] = Counter()
    for f in model.faces.values():
        if f.surface_type == "unknown":
            warnings.append(f"face {f.index}: missing or unsupported surface")
        elif f.surface_type in ("cylinder", "cone", "sphere") and float(f.surface.get("radius", 0.0)) <= 0.0:
            if f.surface_type != "cone" or float(f.surface.get("radius", 0.0)) < 0.0:
                errors.append(f"face {f.index}: invalid {f.surface_type} radius")
        elif f.surface_type == "nurbs":
            ns: NurbsSurface | None = f.surface.get("nurbs")
            if ns is not None and not ns.poles:
                errors.append(f"face {f.index}: empty NURBS surface pole grid")

        if not f.loops:
            warnings.append(f"face {f.index}: face has no bounding loops")
        for loop in f.loops:
            if not loop.coedges:
                errors.append(f"face {f.index} loop {loop.index}: empty loop")
                continue
            m = len(loop.coedges)
            for i, ce in enumerate(loop.coedges):
                edge_use_counts[ce.edge_index] += 1
                if ce.edge_index not in model.edges:
                    errors.append(f"face {f.index} loop {loop.index}: missing edge {ce.edge_index}")
                for v_idx in (ce.start_vertex, ce.end_vertex):
                    if v_idx is not None and v_idx not in model.vertices:
                        errors.append(f"face {f.index} loop {loop.index}: missing vertex {v_idx}")
                nxt = loop.coedges[(i + 1) % m]
                if (
                    ce.end_vertex is not None
                    and nxt.start_vertex is not None
                    and ce.end_vertex != nxt.start_vertex
                ):
                    errors.append(
                        f"face {f.index} loop {loop.index}: open chain between fin {ce.fin_index} "
                        f"(v={ce.end_vertex}) and fin {nxt.fin_index} (v={nxt.start_vertex})"
                    )

    unpaired = [eid for eid, cnt in edge_use_counts.items() if cnt == 1]
    if unpaired and len(model.bodies) == 1 and model.bodies[0].get("body_type") == 1:
        warnings.append(f"{len(unpaired)} boundary edge(s) used only once in solid body")

    stats = summarize(source)
    return ValidationReport(valid=not errors, errors=errors, warnings=warnings, stats=stats)


def summarize(source: ParseResult | BrepModel) -> dict[str, Any]:
    """Return a concise statistical and topological summary of an XT model."""
    parsed, model = _ensure_model(source)
    surf_hist = Counter(f.surface_type for f in model.faces.values())
    curve_hist = Counter(e.curve_type for e in model.edges.values())
    node_hist: dict[str, int] = {}
    header_dict: dict[str, Any] | None = asdict(model.header) if model.header is not None else None
    if parsed is not None:
        node_hist = dict(Counter(n.name for n in parsed.nodes))
        header_dict = asdict(parsed.header)

    return {
        "header": header_dict,
        "bodies": model.bodies,
        "counts": {
            "nodes": len(parsed.nodes) if parsed is not None else None,
            "faces": len(model.faces),
            "edges": len(model.edges),
            "vertices": len(model.vertices),
            "attributes": len(model.attributes),
        },
        "euler": model.euler_characteristic(),
        "bbox": model.bbox(),
        "surface_types": dict(surf_hist),
        "curve_types": dict(curve_hist),
        "node_types": node_hist,
    }


def to_json(source: ParseResult | BrepModel, *, indent: int | None = 2) -> str:
    """Serialize a ParseResult or BrepModel to formatted JSON."""
    if isinstance(source, ParseResult):
        model = extract_brep(source)
        payload = model.to_dict()
        payload["nodes"] = [n.to_dict() for n in source.nodes]
        payload["remaining_bytes"] = source.remaining_bytes
    else:
        payload = source.to_dict()
    return json.dumps(payload, indent=indent, default=str)
