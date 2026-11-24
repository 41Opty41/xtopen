"""Optional OpenCASCADE (OCP / cadquery) integration bridge.

All OCP imports are lazy so core xtopen retains zero external runtime dependencies.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

from .model import BrepModel
from .step_writer import export_step
from .xb import ParseResult


def _require_ocp() -> None:
    try:
        import OCP.TopoDS  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "OCP (cadquery-ocp) is required for xtopen.occt_bridge. Install with: pip install cadquery-ocp"
        ) from exc


def to_occt_shape(source: ParseResult | BrepModel, *, units: str = "mm") -> Any:
    """Convert an XT ParseResult or BrepModel into an OCCT TopoDS_Shape."""
    _require_ocp()
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        export_step(source, tmp_path, units=units)
        reader = STEPControl_Reader()
        status = reader.ReadFile(str(tmp_path))
        if status != IFSelect_RetDone:
            raise RuntimeError(f"OCCT STEPControl_Reader failed with status {status}")
        reader.TransferRoots()
        return reader.OneShape()
    finally:
        tmp_path.unlink(missing_ok=True)


def shape_properties(shape: Any) -> dict[str, Any]:
    """Compute OCCT BRepCheck validity, volume, surface area, and subshape counts."""
    _require_ocp()
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer

    def _count(kind: Any) -> int:
        exp = TopExp_Explorer(shape, kind)
        n = 0
        while exp.More():
            n += 1
            exp.Next()
        return n

    sprops = GProp_GProps()
    vprops = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sprops)
    BRepGProp.VolumeProperties_s(shape, vprops)
    checker = BRepCheck_Analyzer(shape)
    return {
        "valid": bool(checker.IsValid()),
        "volume": float(vprops.Mass()),
        "surface_area": float(sprops.Mass()),
        "solids": _count(TopAbs_SOLID),
        "faces": _count(TopAbs_FACE),
        "edges": _count(TopAbs_EDGE),
        "vertices": _count(TopAbs_VERTEX),
    }


def export_step_occt(source: ParseResult | BrepModel, path: str | Path, *, units: str = "mm") -> Path:
    """Export an XT model to STEP using OCCT STEPControl_Writer."""
    _require_ocp()
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    shape = to_occt_shape(source, units=units)
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    out = Path(path)
    writer.Write(str(out))
    return out
