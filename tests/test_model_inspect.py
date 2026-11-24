"""Tests for B-rep extraction, validation, summary, and JSON output."""

from __future__ import annotations

import json

from tests.conftest import make_box, make_ellipse_and_intersection_sheets, make_rational_nurbs_patch
from xtopen import extract_brep, summarize, to_json, validate


def test_extract_brep_box_topology_and_bbox() -> None:
    model = extract_brep(make_box(1.0, 2.0, 3.0))
    assert len(model.bodies) == 1
    assert len(model.faces) == 6
    assert len(model.edges) == 12
    assert len(model.vertices) == 8
    assert model.euler_characteristic() == 2
    assert model.bbox() == ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0))


def test_rational_nurbs_and_pcurve_extraction() -> None:
    model = extract_brep(make_rational_nurbs_patch())
    assert len(model.faces) == 2
    nurbs_face = next(f for f in model.faces.values() if f.surface_type == "nurbs")
    ns = nurbs_face.surface["nurbs"]
    assert ns.rational is True
    assert ns.weights is not None
    assert len(ns.poles) == 3 and len(ns.poles[0]) == 2

    pcurve_edges = [e for e in model.edges.values() if e.curve_type == "nurbs" and e.curve["nurbs"].is_pcurve]
    assert len(pcurve_edges) == 1
    assert pcurve_edges[0].curve["nurbs"].vertex_dim == 2
    assert len(pcurve_edges[0].curve["nurbs"].poles_2d) == 3


def test_validate_and_json() -> None:
    parsed = make_box(2.0, 2.0, 2.0)
    rep = validate(parsed)
    assert rep.valid is True
    assert rep.errors == []

    # Break vertex coordinate to NaN and confirm validate catches it
    model = extract_brep(parsed)
    first_v = next(iter(model.vertices.values()))
    first_v.point = (float("nan"), 0.0, 0.0)
    bad_rep = validate(model)
    assert bad_rep.valid is False
    assert any("non-finite" in e for e in bad_rep.errors)

    info = summarize(parsed)
    assert info["euler"] == 2
    loaded = json.loads(to_json(parsed))
    assert loaded["counts"]["faces"] == 6


def test_ellipse_intersection_halfedge_fallback_and_none_axis() -> None:
    model = extract_brep(make_ellipse_and_intersection_sheets())
    ctypes = {e.curve_type for e in model.edges.values()}
    assert "ellipse" in ctypes
    assert "intersection" in ctypes
    int_edge = next(e for e in model.edges.values() if e.curve_type == "intersection")
    assert len(int_edge.curve["points"]) == 3
