"""Tests for optional OpenCASCADE bridge (skipped if OCP is absent)."""

from __future__ import annotations

import pytest

from tests.conftest import make_box, make_cylinder
from xtopen.occt_bridge import export_step_occt, shape_properties, to_occt_shape


def test_occt_bridge_box_and_cylinder(tmp_path) -> None:
    pytest.importorskip("OCP")

    box_shape = to_occt_shape(make_box(1.0, 2.0, 3.0), units="m")
    props = shape_properties(box_shape)
    assert props["valid"] is True
    assert props["solids"] == 1
    assert props["faces"] == 6
    # OCCT STEPControl_Reader normalizes file units to mm
    assert props["volume"] == pytest.approx(6.0 * 1e9, rel=1e-3)
    assert props["surface_area"] == pytest.approx(22.0 * 1e6, rel=1e-3)

    cyl_shape = to_occt_shape(make_cylinder(0.5, 2.0), units="mm")
    cprops = shape_properties(cyl_shape)
    assert cprops["valid"] is True
    assert cprops["volume"] == pytest.approx(1e9 * 3.141592653589793 * 0.25 * 2.0, rel=1e-3)

    out = export_step_occt(make_box(1.0, 1.0, 1.0), tmp_path / "occt.step", units="m")
    assert out.exists() and out.stat().st_size > 200
