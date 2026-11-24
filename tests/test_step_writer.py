"""Tests for ISO-10303-21 STEP AP214/AP242 export and OCCT verification."""

from __future__ import annotations

import math

import pytest

from tests.conftest import (
    make_box,
    make_cone_frustum,
    make_cylinder,
    make_ellipse_and_intersection_sheets,
    make_plate_with_hole,
    make_rational_nurbs_patch,
    make_sphere_shell,
    make_torus_sheet,
    make_two_boxes,
)
from xtopen import dumps_step, export_step


def test_dumps_step_ap214_and_ap242_headers(box_model) -> None:
    s214 = dumps_step(box_model, schema="AP214", units="mm")
    assert "ISO-10303-21;" in s214
    assert "AUTOMOTIVE_DESIGN" in s214
    assert "SI_UNIT(.MILLI.,.METRE.)" in s214
    assert "MANIFOLD_SOLID_BREP" in s214

    s242 = dumps_step(box_model, schema="AP242", units="m")
    assert "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF" in s242
    assert "SI_UNIT($,.METRE.)" in s242


def _read_step_occt(step_path):
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(step_path))
    assert status == IFSelect_RetDone
    reader.TransferRoots()
    shape = reader.OneShape()
    checker = BRepCheck_Analyzer(shape)
    sp = GProp_GProps()
    vp = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sp)
    BRepGProp.VolumeProperties_s(shape, vp)
    return shape, bool(checker.IsValid()), float(vp.Mass()), float(sp.Mass())


@pytest.mark.parametrize(
    "builder,args,expected_vol,expected_area",
    [
        (make_box, (1.0, 2.0, 3.0), 6.0, 22.0),
        (make_cylinder, (0.5, 2.0), math.pi * 0.25 * 2.0, 2.0 * math.pi * 0.25 + 2.0 * math.pi * 0.5 * 2.0),
        (
            make_cone_frustum,
            (1.0, 0.4, 2.0),
            (math.pi * 2.0 / 3.0) * (1.0 + 0.4 + 0.16),
            math.pi * 1.0 + math.pi * 0.16 + math.pi * (1.0 + 0.4) * math.sqrt(0.6**2 + 2.0**2),
        ),
        (
            make_plate_with_hole,
            (4.0, 4.0, 1.0, 0.5),
            16.0 - math.pi * 0.25,
            2.0 * (16.0 - math.pi * 0.25) + 4.0 * 4.0 + 2.0 * math.pi * 0.5 * 1.0,
        ),
        (
            make_sphere_shell,
            (1.0,),
            (4.0 / 3.0) * math.pi,
            4.0 * math.pi,
        ),
    ],
)
def test_step_roundtrip_occt_solids(tmp_path, builder, args, expected_vol, expected_area) -> None:
    pytest.importorskip("OCP")
    parsed = builder(*args)
    stp = tmp_path / f"{builder.__name__}.step"
    export_step(parsed, stp, units="m")
    _, valid, vol, area = _read_step_occt(stp)
    assert valid is True
    # OCCT STEPControl_Reader scales meters to internal mm (1e9 volume, 1e6 area)
    assert vol == pytest.approx(expected_vol * 1e9, rel=1e-3)
    assert area == pytest.approx(expected_area * 1e6, rel=1e-3)


def test_step_roundtrip_occt_torus_and_rational_nurbs_sheets(tmp_path) -> None:
    pytest.importorskip("OCP")

    tor_stp = tmp_path / "torus.step"
    export_step(make_torus_sheet(1.0, 0.25), tor_stp, units="mm")
    _, valid_t, _, area_t = _read_step_occt(tor_stp)
    assert valid_t is True
    assert area_t > 0.0

    nurbs_stp = tmp_path / "nurbs.step"
    export_step(make_rational_nurbs_patch(), nurbs_stp, units="m")
    _, valid_n, _, area_n = _read_step_occt(nurbs_stp)
    assert valid_n is True
    # Quarter cylinder (r=1, h=1 -> pi/2) + unit right triangle p-curve sheet (0.5), scaled to mm^2
    assert area_n == pytest.approx((math.pi / 2.0 + 0.5) * 1e6, rel=1e-3)


def test_step_multibody_ellipse_and_intersection_occt(tmp_path) -> None:
    pytest.importorskip("OCP")
    from xtopen.occt_bridge import shape_properties, to_occt_shape

    multi = make_two_boxes()
    props = shape_properties(to_occt_shape(multi, units="m"))
    assert props["valid"] is True
    assert props["solids"] == 2
    assert props["volume"] == pytest.approx(3.0 * 1e9, rel=1e-3)

    sh_ell = tmp_path / "ell_int.step"
    export_step(make_ellipse_and_intersection_sheets(), sh_ell, units="m")
    _, valid_e, _, area_e = _read_step_occt(sh_ell)
    assert valid_e is True
    # Quarter ellipse (pi*2*1/4 = pi/2) + triangle (0.5), scaled to mm^2
    assert area_e == pytest.approx((math.pi / 2.0 + 0.5) * 1e6, rel=1e-3)
