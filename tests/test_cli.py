"""End-to-end tests for the xtopen CLI."""

from __future__ import annotations

import json

from xtopen import write_xb
from xtopen.cli import main


def test_cli_info_validate_dump_convert(tmp_path, box_model, capsys) -> None:
    xb_path = tmp_path / "box.x_b"
    xb_path.write_bytes(write_xb(box_model))

    assert main(["info", str(xb_path), "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["counts"]["faces"] == 6

    assert main(["validate", str(xb_path), "--json"]) == 0
    val = json.loads(capsys.readouterr().out)
    assert val["valid"] is True

    dump_json = tmp_path / "dump.json"
    assert main(["dump", str(xb_path), "-o", str(dump_json)]) == 0
    assert json.loads(dump_json.read_text(encoding="utf-8"))["euler"] == 2

    xt_path = tmp_path / "box.x_t"
    assert main(["convert", str(xb_path), str(xt_path)]) == 0
    assert xt_path.exists()

    step_path = tmp_path / "box.step"
    assert main(["convert", str(xt_path), str(step_path), "--units", "mm", "--schema", "AP242"]) == 0
    text = step_path.read_text(encoding="ascii")
    assert "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF" in text
