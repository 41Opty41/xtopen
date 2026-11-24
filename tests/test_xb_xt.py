"""Tests for .x_b, .x_t, .x_d parsing and roundtrip serialization."""

from __future__ import annotations

import struct

from tests.conftest import make_box
from xtopen import parse_delta, parse_xb, parse_xt, write_xb, write_xt, xb_to_xt, xt_to_xb
from xtopen.xb import _MAGIC_FILL


def test_xb_form1_and_form2_roundtrip(box_model) -> None:
    for form2 in (False, True):
        data = write_xb(box_model, form2=form2)
        parsed = parse_xb(data)
        assert parsed.remaining_bytes == 0
        assert parsed.header.form2 == form2
        assert len(parsed.nodes) == len(box_model.nodes)
        for a, b in zip(box_model.nodes, parsed.nodes):
            assert a.type == b.type
            assert a.index == b.index
            assert a.fields == b.fields


def test_xt_write_parse_and_transcode_roundtrip(box_model) -> None:
    xt_bytes = write_xt(box_model)
    parsed_xt = parse_xt(xt_bytes)
    assert parsed_xt.remaining_bytes == 0
    assert len(parsed_xt.nodes) == len(box_model.nodes)

    xb_bytes = xt_to_xb(xt_bytes)
    xt_again = xb_to_xt(xb_bytes)
    parsed_again = parse_xt(xt_again)
    assert len(parsed_again.nodes) == len(box_model.nodes)


def test_parse_delta_minimal_synthetic() -> None:
    # Build minimal .x_d delta container with common header + PS\x00\x00(deltas) + one 0x00ef PMARK chunk
    bm = make_box(1.0, 1.0, 1.0)
    bm.header.schema_name = "SCH_12000(deltas)"
    body = bytearray(write_xb(bm))
    ps_off = body.index(b"PS\x00\x00")
    mlen = struct.unpack(">h", body[ps_off + 4 : ps_off + 6])[0]
    slen_off = ps_off + 6 + mlen
    slen = struct.unpack(">i", body[slen_off : slen_off + 4])[0]
    hdr_end = slen_off + 4 + slen + 4
    stream_prefix = body[:hdr_end]
    rec_start = b"\x00\xef\x00\x00"
    rec_payload = b"\x00\x01\x00\x01" + _MAGIC_FILL
    res = parse_delta(bytes(stream_prefix + b"(deltas)" + rec_start + b"\x00" * 12 + rec_payload))
    assert len(res.transactions) == 1
