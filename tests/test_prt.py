"""Tests for clean-room NX .prt container extraction, B-rep merging, and CLI."""

from __future__ import annotations

from pathlib import Path
import struct

import pytest

from tests.conftest import make_box, make_plate_with_hole
from xtopen import (
    build_ole2_prt,
    build_splmsstr_prt,
    dumps_step,
    export_step,
    extract_prt,
    extract_prt_brep,
    is_prt,
    load_prt,
    parse_prt,
    validate,
    write_xb,
)
from xtopen.cli import main as cli_main
from xtopen.model import extract_brep
from xtopen.prt import parse_ole2_cfb, parse_splmsstr
from xtopen.xb import parse_xb

try:
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    HAS_OCCT = True
except ImportError:
    HAS_OCCT = False


def _load_occt_step_vol(step_path: Path) -> tuple[bool, float]:
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(step_path))
    assert status == IFSelect_RetDone
    assert reader.TransferRoots() >= 1
    shp = reader.OneShape()
    valid = bool(BRepCheck_Analyzer(shp).IsValid())
    vp = GProp_GProps()
    BRepGProp.VolumeProperties_s(shp, vp)
    return valid, float(vp.Mass())


def test_splmsstr_compressed_roundtrip_and_preview(tmp_path: Path) -> None:
    # plate_with_hole default in tests.conftest (units='m' -> STEP units='m')
    plate_xb = write_xb(make_plate_with_hole())
    fake_jpg = b"\xff\xd8\xff\xe0" + b"JPEGDATA" * 8
    prt_bytes = build_splmsstr_prt([plate_xb], compress=True, preview=fake_jpg)

    assert is_prt(prt_bytes)
    entries = parse_splmsstr(prt_bytes)
    assert "/Root/UG_PART/UG_PART" in entries
    assert entries["/Root/images/preview"] == fake_jpg

    arch = extract_prt(prt_bytes)
    assert arch.container == "splmsstr"
    assert arch.preview == fake_jpg
    assert len(arch.streams) == 1
    assert arch.streams[0].compressed is True
    assert arch.streams[0].kind == "body"
    assert arch.streams[0].body_count == 1
    assert arch.streams[0].face_count == 8

    parsed = parse_prt(prt_bytes)
    assert parsed.header.format_kind == "nx-prt-splmsstr"
    rep = validate(parsed)
    assert rep.valid, rep.errors

    prt_file = tmp_path / "plate.prt"
    prt_file.write_bytes(prt_bytes)
    loaded = load_prt(prt_file)
    assert len(loaded.nodes) > 0

    step_text = dumps_step(extract_prt_brep(prt_bytes), schema="AP214", units="m", name="plate_prt")
    assert "CLOSED_SHELL" in step_text
    if HAS_OCCT:
        step_file = tmp_path / "plate.step"
        step_file.write_text(step_text, encoding="ascii")
        valid, vol = _load_occt_step_vol(step_file)
        assert valid
        assert abs(vol / 1e9 - 15.2146018366) < 1e-4


def test_splmsstr_uncompressed_and_multistream_dedup_merge() -> None:
    box_xb = write_xb(make_box(1.0, 2.0, 3.0))
    plate_xb = write_xb(make_plate_with_hole())
    # Include a duplicate of box_xb to verify deduplication.
    prt_bytes = build_splmsstr_prt([box_xb, box_xb, plate_xb], compress=False)

    arch = extract_prt(prt_bytes)
    assert len(arch.streams) == 3
    merged = extract_prt_brep(prt_bytes)
    # 1 box (6 faces) + 1 plate_with_hole (8 faces) = 2 bodies, 14 faces (duplicate ignored)
    assert len(merged.bodies) == 2
    assert len(merged.faces) == 14
    step = dumps_step(merged, schema="AP242", units="mm", name="multi")
    assert step.count("CLOSED_SHELL") == 2


def test_ole2_cfb_container_extraction_and_step(tmp_path: Path) -> None:
    box_xb = write_xb(make_box(1.0, 2.0, 3.0))
    prt_bytes = build_ole2_prt([box_xb])

    assert is_prt(prt_bytes)
    streams = parse_ole2_cfb(prt_bytes)
    assert "UG_PART" in streams

    arch = extract_prt(prt_bytes)
    assert arch.container == "ole2"
    assert len(arch.streams) == 1
    assert arch.streams[0].face_count == 6

    step_file = tmp_path / "ole2_box.step"
    export_step(extract_prt_brep(prt_bytes), step_file, units="m", name="ole2_box")
    if HAS_OCCT:
        valid, vol = _load_occt_step_vol(step_file)
        assert valid
        assert abs(vol / 1e9 - 6.0) < 1e-5


def test_empty_prt_raises_clean_error() -> None:
    empty_ug = b"NO_PARASOLID_HERE" * 4
    entries = [("/Root/", None), ("/Root/UG_PART/", None), ("/Root/UG_PART/UG_PART", empty_ug)]
    out = bytearray(b"SPLMSSTR\x06" + b"\x00" * 16 + b"HEADER")
    out.extend(struct.pack("<I", len(entries)))
    dir_size = sum(4 + len(p) + 16 for p, _ in entries)
    ug_off = 35 + dir_size
    for p, blob in entries:
        pb = p.encode("ascii")
        out.extend(struct.pack("<I", len(pb)) + pb)
        out.extend(b"\x00" * 16 if blob is None else struct.pack("<QQ", ug_off, len(blob)))
    struct.pack_into("<QQ", out, 9, ug_off, ug_off + len(empty_ug))
    out.extend(empty_ug)

    with pytest.raises(ValueError, match="no embedded Parasolid"):
        extract_prt_brep(bytes(out))


def test_cli_prt_info_convert_extract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    box_xb = write_xb(make_box(1.0, 2.0, 3.0))
    prt_bytes = build_splmsstr_prt([box_xb], compress=True, preview=b"\xff\xd8\xff\xd9")
    prt_path = tmp_path / "sample.prt"
    prt_path.write_bytes(prt_bytes)

    assert cli_main(["info", str(prt_path)]) == 0
    out = capsys.readouterr().out
    assert "NX Container: splmsstr" in out
    assert "Faces: 6" in out

    step_out = tmp_path / "sample.step"
    assert cli_main(["convert", str(prt_path), str(step_out), "--units", "mm"]) == 0
    assert step_out.exists() and "CLOSED_SHELL" in step_out.read_text(encoding="ascii")

    ext_dir = tmp_path / "extracted"
    assert cli_main(["extract-prt", str(prt_path), str(ext_dir)]) == 0
    assert (ext_dir / "sample_stream00.x_b").exists()
    assert (ext_dir / "sample_preview.jpg").exists()
    rep = parse_xb((ext_dir / "sample_stream00.x_b").read_bytes())
    assert len(extract_brep(rep).faces) == 6
