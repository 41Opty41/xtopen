"""Tests for built-in XT schema and optional .s_t loader."""

from __future__ import annotations

from xtopen.schema import builtin_schema, effective, parse_schema


def test_builtin_schema_contains_core_nodes() -> None:
    s = builtin_schema()
    assert 12 in s and s[12]["name"] == "BODY"
    assert 14 in s and s[14]["name"] == "FACE"
    assert 50 in s and s[50]["name"] == "PLANE"
    assert 126 in s and s[126]["name"] == "NURBS_SURF"
    assert 136 in s and s[136]["name"] == "NURBS_CURVE"
    eff = effective(s[12])
    assert any(f["name"] == "nom_geom_state" for f in eff)


def test_parse_schema_synthetic_file(tmp_path) -> None:
    st = tmp_path / "mini.s_t"
    st.write_text(
        "**END_OF_HEADER\n"
        "29 POINT; ; 1 0 0\n"
        "  node_id; d; 1 0 0\n"
        "  pvec; v; 1 0 0\n",
        encoding="ascii",
    )
    parsed = parse_schema(st)
    assert 29 in parsed
    assert parsed[29]["name"] == "POINT"
    assert [f["name"] for f in effective(parsed[29])] == ["node_id", "pvec"]
