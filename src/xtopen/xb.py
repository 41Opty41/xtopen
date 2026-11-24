"""Clean-room binary Parasolid XT (.x_b, .x_p, .x_d) parser.

Supports:
- Neutral binary Form-1 (classic) and Form-2 (schema-embedded) .x_b files.
- Partition transmit files (.x_p) with text headers.
- Delta transmit files (.x_d) with PMARK transaction framing.
- Built-in self-contained XT schema by default (no external .s_t file needed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import struct
from typing import Any

from .schema import builtin_schema, effective

UNSET_I = -32764
UNSET_R = -3.14158e13
_MAGIC_FILL = b"\xc2\xbc\x92\x8f\x99\x6e\x00\x00"

_ATTR_SIZES = (32, 42)
_TOKEN_SIZES = (44, 62, 65, 75, 96, 122, 124, 126, 139, 161, 183, 184, 189, 191)
_DOUBLE_SIZES = (8, 26, 33, 58, 64, 152)


@dataclass
class XtHeader:
    """Metadata decoded from the XT common text header and binary stream prefix."""

    format_kind: str = "binary"
    modeler: str = ""
    schema_name: str = ""
    max_node: int | None = None
    user_field_size: int = 0
    form2: bool = False
    common_header: dict[str, str] = field(default_factory=dict)


@dataclass
class XtNode:
    """Single node record decoded from an XT binary or text stream."""

    type: int
    name: str
    index: int
    fields: list[tuple[str, Any]]
    varlen: int | None = None
    offset: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        for k, v in self.fields:
            if k == key:
                if isinstance(v, list) and len(v) == 1:
                    return v[0]
                return v
        return default

    def get_list(self, key: str, default: list | None = None) -> list:
        for k, v in self.fields:
            if k == key:
                return list(v) if isinstance(v, (list, tuple)) else [v]
        return [] if default is None else default

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "index": self.index,
            "varlen": self.varlen,
            "fields": list(self.fields),
            "offset": self.offset,
        }


@dataclass
class ParseResult:
    """Result of parsing an .x_b, .x_p, or .x_t file."""

    header: XtHeader
    nodes: list[XtNode]
    remaining_bytes: int = 0
    layouts: dict[int, list[dict]] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)

    def by_index(self) -> dict[int, XtNode]:
        return {n.index: n for n in self.nodes}


@dataclass
class DeltaResult:
    """Result of parsing a delta transmit (.x_d) file."""

    header: XtHeader
    transactions: list[dict[str, Any]]
    remaining_bytes: int = 0


class Cur:
    """Binary cursor with configurable endianness and XT index/scalar decoding."""

    def __init__(self, data: bytes, pos: int = 0, le: bool = True) -> None:
        self.d = data
        self.p = pos
        self.le = le
        self.form2 = False

    def take(self, n: int) -> bytes:
        v = self.d[self.p : self.p + n]
        if len(v) < n:
            raise EOFError(f"Unexpected EOF at byte {self.p} (wanted {n}, got {len(v)})")
        self.p += n
        return v

    def peek(self, n: int) -> bytes:
        return self.d[self.p : self.p + n]

    def u8(self) -> int:
        return self.take(1)[0]

    def i16(self) -> int | None:
        v = struct.unpack("<h" if self.le else ">h", self.take(2))[0]
        return None if v == UNSET_I else v

    def u16(self) -> int:
        return struct.unpack("<H" if self.le else ">H", self.take(2))[0]

    def i32(self) -> int | None:
        v = struct.unpack("<i" if self.le else ">i", self.take(4))[0]
        return None if v == UNSET_I else v

    def f64(self) -> float | None:
        v = struct.unpack("<d" if self.le else ">d", self.take(8))[0]
        return None if v == UNSET_R else v

    def index(self) -> int | None:
        rem = self.i16()
        if rem is None or rem == 0:
            return None
        if rem < 0:
            q = self.u16()
            return q * 32767 + (-rem) - 1
        return rem - 1

    def logical(self) -> bool:
        v = self.u8()
        if v not in (0, 1):
            raise ValueError(f"Invalid logical byte {v} at offset {self.p}")
        return v == 1

    def short_string(self) -> bytes:
        n = self.u8()
        return self.take(n)


def _read_scalar(c: Cur, code: str) -> Any:
    if code == "u":
        return c.u8()
    if code == "c":
        return c.u8()
    if code == "l":
        # Form-2 logical fields can carry raw byte flags/counts
        return c.u8() if c.form2 else c.logical()
    if code == "n":
        return c.i16()
    if code == "w":
        return c.u16()
    if code in ("d", "t"):
        return c.i32()
    if code == "p":
        return c.index()
    if code == "f":
        return c.f64()
    if code == "i":
        return [c.f64(), c.f64()]
    if code == "v":
        return [c.f64(), c.f64(), c.f64()]
    if code == "b":
        return [c.f64() for _ in range(6)]
    if code == "h":
        return [c.f64(), c.f64(), c.f64()]
    raise ValueError(f"Unsupported XT field code: {code!r}")


def _parse_common_header(header_bytes: bytes) -> dict[str, str]:
    text = header_bytes.decode("ascii", "replace")
    # Unwrap newline continuations inside **PART blocks
    meta: dict[str, str] = {}
    for seg in text.replace("\r\n", "\n").replace("\n", "").split(";"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            k = k.strip().lstrip("*")
            if k.startswith("PART"):
                continue
            meta[k] = v.strip()
    return meta


def _strip_common_header(data: bytes) -> tuple[bytes, dict[str, str]]:
    if data[:4] == b"PS\x00\x00":
        return data, {}
    if not data.lstrip(b" \t\r\n").startswith(b"**"):
        raise ValueError("Missing Parasolid common header or PS\\x00\\x00 prefix")
    i = data.find(b"**END_OF_HEADER")
    if i < 0:
        raise ValueError("Missing **END_OF_HEADER marker in XT header")
    j = data.find(b"\n", i)
    if j < 0:
        raise ValueError("Malformed **END_OF_HEADER line")
    j += 1
    while j < len(data) and data[j] in (13, 10):
        j += 1
    return data[j:], _parse_common_header(data[:i])


def _read_embedded_field(c: Cur) -> dict[str, Any]:
    name = c.short_string().decode("ascii", "replace")
    ptr = struct.unpack(">h", c.take(2))[0]
    rem = struct.unpack(">h", c.take(2))[0]
    if rem < 0:
        el = struct.unpack(">H", c.take(2))[0] * 32767 + (-rem)
    elif rem == 0:
        el = 1
    else:
        el = rem - 1
    hi = c.peek(1)[0]
    if hi == 0:
        c.take(1)
        code = c.take(1).decode("ascii", "replace")
        raw = 2
        while c.peek(1) == b"\x00" and raw < 8:
            c.take(1)
            raw += 1
        transmitted = True
    elif ptr != 0:
        code = "p"
        transmitted = True
    else:
        transmitted = c.logical()
        code = c.take(1).decode("ascii", "replace")
    return {
        "name": name,
        "code": code,
        "transmitted": transmitted,
        "ptr_class": ptr,
        "elements": el,
        "rem": rem,
        "rec": True,
    }


def _read_f2_field(c: Cur) -> dict[str, Any]:
    name = c.short_string().decode("ascii", "replace")
    ptr = struct.unpack(">h", c.take(2))[0]
    rem = struct.unpack(">h", c.take(2))[0]
    if rem < 0:
        el = struct.unpack(">H", c.take(2))[0] * 32767 + (-rem)
    elif rem in (0, 1):
        el = 1
    elif rem == 2:
        el = 2
    else:
        el = rem - 1
    if ptr == 0:
        pk2 = c.take(2)
        code = chr(pk2[1])
    else:
        code = "p"
    return {
        "name": name,
        "code": code,
        "transmitted": True,
        "ptr_class": ptr,
        "elements": el,
        "rec": True,
    }


def parse_xb(
    data: bytes,
    schema: dict[int, dict] | None = None,
    *,
    multi: bool = False,
    start: int = 0,
    layouts_from: dict[str, Any] | None = None,
    form2_forced: bool | None = None,
    meta_out: dict[str, Any] | None = None,
) -> ParseResult:
    """Parse neutral binary Parasolid XT (.x_b or .x_p payload) into ParseResult."""
    if schema is None:
        schema = builtin_schema()

    common_meta: dict[str, str] = {}
    if not start:
        data, common_meta = _strip_common_header(data)

    if data[:4] != b"PS\x00\x00":
        raise ValueError(f"Expected PS\\x00\\x00 binary prefix, got {data[:4]!r}")

    c = Cur(data, 4, le=False)
    mlen = struct.unpack(">h", data[4:6])[0]
    modeler = data[6 : 6 + mlen].decode("ascii", "replace")
    c.p = 6 + mlen
    slen = struct.unpack(">i", c.take(4))[0]
    schema_name = c.take(slen).decode("ascii", "replace")
    parts = schema_name.split("_")
    max_node: int | None = None
    if len(parts) == 4 and parts[0] == "SCH" and all(p.isdigit() for p in parts[1:]):
        max_node = struct.unpack(">h", c.take(2))[0]
    ufs = struct.unpack(">i", c.take(4))[0]

    if start:
        c = Cur(data, start, le=False)
        form2 = bool(form2_forced)
        c.form2 = form2
    else:
        pk = c.p
        t1 = struct.unpack(">h", data[pk : pk + 2])[0]
        op1 = data[pk + 3 : pk + 4]
        le1 = struct.unpack("<h", data[pk : pk + 2])[0]
        form2 = (
            (schema_name.startswith("SCH_") and max_node is not None)
            or (
                1 < t1 <= 4096
                and op1 in b"CDIAZ"
                and (t1 not in schema or data[pk + 2] != 255)
                and not (1 < le1 <= 4096 and data[pk + 2] == 255)
            )
        )
        c.form2 = form2

    layouts: dict[int, list[dict]] = {}
    names: dict[int, str] = {}
    if layouts_from:
        layouts.update(layouts_from.get("layouts", {}))
        names.update(layouts_from.get("names", {}))

    nodes: list[XtNode] = []
    ext_types: set[int] = set()

    while True:
        rec_start = c.p
        if form2 and c.peek(8) == _MAGIC_FILL:
            c.take(8)
            continue

        c.le = False
        ntype = c.i16()
        if ntype is None:
            raise ValueError(f"Unset node type at offset {rec_start}")
        if ntype < 0:
            raise ValueError(f"Negative node type {ntype} at offset {rec_start}")
        if ntype == 1:
            if form2:
                c.le = True
            _ = c.index()
            if not multi or c.p >= len(data):
                break
            continue

        node = schema.get(ntype)
        if ntype not in layouts:
            flag = c.u8()
            if node is None:
                if flag == 255:
                    raise ValueError(f"Unknown node type {ntype} with flag 255 at {rec_start}")
                nm = c.short_string().decode("ascii", "replace")
                _desc = c.short_string().decode("ascii", "replace")
                layout = [
                    _read_f2_field(c) if form2 else _read_embedded_field(c)
                    for _ in range(flag)
                ]
                if form2:
                    layout = [f for f in layout if f["name"] != "node_id"]
                layouts[ntype] = layout
                names[ntype] = nm
                if form2:
                    c.take(1)  # 0x01 terminator before count word
                node = {"type": ntype, "name": nm, "variable": form2, "fields": layout}
            elif flag == 255:
                layouts[ntype] = effective(node)
            else:
                base = effective(node)
                out: list[dict] = []
                pos = 0
                append_phase = False
                if form2 and flag not in (1, 255) and c.peek(1) not in (b"C", b"D", b"I", b"A", b"Z"):
                    nm = c.short_string().decode("ascii", "replace")
                    _desc = c.short_string().decode("ascii", "replace")
                    names[ntype] = nm
                    layout = [_read_f2_field(c) for _ in range(flag)]
                    layouts[ntype] = layout
                    node = dict(node)
                    node["variable"] = True
                    c.take(1)
                elif form2 and flag == 1:
                    layouts[ntype] = base
                else:
                    while True:
                        op = c.take(1)
                        if op == b"C":
                            if append_phase:
                                raise ValueError(f"Edit op 'C' after 'A' at {c.p}")
                            out.append(base[pos])
                            pos += 1
                        elif op == b"D":
                            if append_phase:
                                raise ValueError(f"Edit op 'D' after 'A' at {c.p}")
                            pos += 1
                        elif op == b"I":
                            if append_phase:
                                raise ValueError(f"Edit op 'I' after 'A' at {c.p}")
                            out.append(_read_f2_field(c) if form2 else _read_embedded_field(c))
                        elif op == b"A":
                            if pos != len(base):
                                raise ValueError(f"Edit op 'A' before base end at {c.p}")
                            append_phase = True
                            out.append(_read_f2_field(c) if form2 else _read_embedded_field(c))
                        elif op == b"Z":
                            break
                        else:
                            raise ValueError(f"Unknown schema edit op {op!r} at {c.p}")
                    if len(out) != flag:
                        raise ValueError(f"Schema edit produced {len(out)} fields, expected {flag}")
                    layouts[ntype] = out

        layout = layouts[ntype]
        if node is None:
            node = {
                "type": ntype,
                "name": names.get(ntype, f"type_{ntype}"),
                "variable": False,
                "fields": layout,
            }

        if form2:
            c.le = False

        varlen: int | None = None
        idx_done = False
        if form2:
            if ntype in ext_types:
                varlen = c.i32()
                fidx = c.index()
                if fidx is None:
                    raise ValueError(f"Zero node index at offset {rec_start}")
                idx_done = True
            elif node["variable"]:
                varlen = c.i32()
        elif node["variable"]:
            varlen = c.i32()

        if not idx_done:
            fidx = c.index()
            if fidx is None:
                raise ValueError(f"Zero node index at offset {rec_start}")

        if form2 and ntype in names:
            ext_types.add(ntype)

        fields: list[tuple[str, Any]] = []
        for f in layout:
            if not f["transmitted"]:
                continue
            el = f["elements"]
            if el == 0:
                count = 1
            elif form2 and f.get("rec"):
                if el == 2:
                    count = varlen if varlen is not None else 2
                else:
                    count = el
            elif el == 1:
                if f["name"] == "values" and ntype == 204 and not form2:
                    uv = dict(fields).get("uv_type", [1])[0]
                    count = {0: 0, 1: 2, 2: 3}.get(uv, 2)
                elif varlen is not None:
                    count = varlen
                elif form2:
                    count = 1
                else:
                    count = c.i32()
            elif el == 2 and form2:
                count = 2
            elif el == 2:
                count = c.i32()
            else:
                count = el

            if f["code"] == "c":
                vals: Any = list(c.take(count))
            else:
                vals = [_read_scalar(c, f["code"]) for _ in range(count)]
            fields.append((f["name"], vals))

        nodes.append(
            XtNode(
                type=ntype,
                name=node["name"],
                index=fidx,
                fields=fields,
                varlen=varlen,
                offset=rec_start,
            )
        )

    if meta_out is not None:
        meta_out["layouts"] = layouts
        meta_out["names"] = names

    header = XtHeader(
        format_kind="binary_partition" if common_meta.get("GUISE") == "transmit_partition" else "binary",
        modeler=modeler,
        schema_name=schema_name,
        max_node=max_node,
        user_field_size=ufs,
        form2=form2,
        common_header=common_meta,
    )
    return ParseResult(
        header=header,
        nodes=nodes,
        remaining_bytes=len(data) - c.p,
        layouts=layouts,
        names=names,
    )


def _delta_chunks(data: bytes) -> tuple[bytes, list[tuple[int, int]], list[bytes]]:
    mlen = struct.unpack(">h", data[4:6])[0]
    slen = struct.unpack(">i", data[6 + mlen : 10 + mlen])[0]
    s = 10 + mlen + slen
    starts = sorted(s + m.start() for m in re.finditer(b"\x00[\xe7\xef]\x00\x00", data[s:]))
    if not starts:
        return data, [], []
    decls = sorted(
        s + m.start()
        for m in re.finditer(rb"([A-Za-z]{2,})([\x02-\x40])([a-z_]{2,64})", data[s:])
        if m.group(2)[0] == len(m.group(3))
    )
    header = data[: starts[0]]
    txns, decls_out = [], []
    for k, p in enumerate(starts):
        mg = data.find(_MAGIC_FILL, p + 16)
        nxt = starts[k + 1] if k + 1 < len(starts) else len(data)
        if mg < 0:
            txns.append((p, nxt))
            continue
        txns.append((p, mg + 8))
        d0 = next((d for d in decls if d > mg), None)
        if d0 is not None and d0 < nxt:
            decls_out.append(data[d0:nxt])
    return header, txns, decls_out


def _delta_attr8(data: bytes, a: int, b: int) -> list[int] | None:
    tags, p, prev = [], a, 0
    for _ in range(8):
        if p >= b:
            return None
        byte = data[p]
        p += 1
        if byte == 0xFF:
            if p + 2 > b:
                return None
            prev = int.from_bytes(data[p : p + 2], "big")
            p += 2
        elif byte == 0:
            prev += 1
        elif byte <= 0x7F:
            prev += byte
        else:
            prev -= 256 - byte
        if prev < 0:
            return None
        tags.append(prev)
    return tags


def _delta_token_fields(data: bytes, a: int, b: int) -> tuple[list[tuple], bool]:
    fields, p, clean = [], a, True
    while p < b:
        if p + 3 <= b and data[p + 2] in (0x00, 0x01):
            fields.append((p, "F", (data[p] << 8) | data[p + 1], data[p + 2]))
            p += 3
        elif data[p] in (0x2D, 0x2B):
            fields.append((p, "S", data[p], None))
            p += 1
        elif p + 2 <= b:
            fields.append((p, "B", (data[p] << 8) | data[p + 1], None))
            p += 2
        else:
            fields.append((p, "X", data[p], None))
            p += 1
            clean = False
    return fields, clean


def _delta_doubles(data: bytes, a: int, b: int) -> list[float]:
    out, p = [], a
    while p + 8 <= b:
        v = struct.unpack(">d", data[p : p + 8])[0]
        if v == v and abs(v) <= 1e12:
            out.append(v)
            p += 8
        else:
            p += 1
    return out


def _delta_copies_all(data: bytes, p0: int, p1: int) -> list[dict[str, Any]]:
    ms, pos = [], p0
    while True:
        i = data.find(_MAGIC_FILL, pos, p1)
        if i < 0:
            break
        ms.append(i)
        pos = i + 8
    out = []
    for k in range(len(ms) - 1):
        a, b = ms[k] + 8, ms[k + 1]
        size = b - a
        if size < 8:
            continue
        if size > 200:
            kind, payload = "values", _delta_doubles(data, a, b)[:40]
        elif size in _ATTR_SIZES:
            tags = _delta_attr8(data, a, b)
            kind, payload = ("attr", tags) if tags else ("other", None)
        elif size in _TOKEN_SIZES:
            fields, clean = _delta_token_fields(data, a, b)
            kind, payload = ("tokens", fields) if (clean and len(fields) >= 8) else ("other", None)
        elif size in _DOUBLE_SIZES:
            vals = _delta_doubles(data, a, b)
            kind, payload = ("doubles", vals) if len(vals) * 8 >= size - 8 else ("other", None)
        else:
            kind, payload = "other", None
        out.append({"offset": a, "size": size, "kind": kind, "data": payload})
    return out


def parse_delta(data: bytes, schema: dict[int, dict] | None = None) -> DeltaResult:
    """Parse a Parasolid delta transmit (.x_d) binary stream."""
    if schema is None:
        schema = builtin_schema()
    stripped, common_meta = _strip_common_header(data)
    if b"(deltas)" not in stripped[:96]:
        raise ValueError("Not a Parasolid delta transmit stream")

    mlen = struct.unpack(">h", stripped[4:6])[0]
    modeler = stripped[6 : 6 + mlen].decode("ascii", "replace")
    slen = struct.unpack(">i", stripped[6 + mlen : 10 + mlen])[0]
    schema_name = stripped[10 + mlen : 10 + mlen + slen].decode("ascii", "replace")

    txns: list[dict[str, Any]] = []
    try:
        res = parse_xb(data, schema)
        rem = res.remaining_bytes
        cur: dict[str, Any] | None = None
        for n in res.nodes:
            if n.name == "PMARK":
                cur = {
                    "pmark": dict(n.fields),
                    "pmark_index": n.index,
                    "nodes": [],
                    "pmark_offset": n.offset,
                }
                txns.append(cur)
            elif cur is not None:
                cur["nodes"].append(n)
        for i, t in enumerate(txns):
            a = t.pop("pmark_offset", 0)
            b = txns[i + 1]["pmark_offset"] if i + 1 < len(txns) else len(stripped)
            t["copies"] = _delta_copies_all(stripped, a, b)
    except (ValueError, EOFError):
        _, txns_raw, decls = _delta_chunks(stripped)
        meta: dict[str, Any] = {"layouts": {}, "names": {}}
        rem = sum(len(d) for d in decls)
        for k, (p0, p1) in enumerate(txns_raw):
            mg = stripped.find(_MAGIC_FILL, p0 + 16, p1)
            if mg < 0:
                txns.append({"pmark": {}, "pmark_index": None, "nodes": [], "payload_bytes": p1 - p0})
                continue
            chunk_nodes: list[XtNode] = []
            try:
                sub = parse_xb(
                    stripped,
                    schema,
                    start=mg + 7,
                    layouts_from=meta,
                    form2_forced=True,
                    meta_out=meta,
                )
                chunk_nodes = sub.nodes
                rem_after = sub.remaining_bytes
            except (ValueError, EOFError):
                rem_after = len(stripped)
            decoded = (len(stripped) - rem_after) - (mg + 7)
            nxt = txns_raw[k + 1][0] if k + 1 < len(txns_raw) else len(stripped)
            cur_txn: dict[str, Any] | None = None
            for n in chunk_nodes:
                if n.name == "PMARK":
                    cur_txn = {
                        "pmark": dict(n.fields),
                        "pmark_index": n.index,
                        "nodes": [],
                        "payload_bytes": max(0, (p1 - (mg + 7)) - decoded),
                        "copies": _delta_copies_all(stripped, mg + 8, nxt),
                    }
                    txns.append(cur_txn)
                elif cur_txn is not None:
                    cur_txn["nodes"].append(n)
            if cur_txn is None:
                txns.append(
                    {
                        "pmark": {},
                        "pmark_index": None,
                        "nodes": chunk_nodes,
                        "payload_bytes": max(0, (p1 - (mg + 7)) - decoded),
                        "copies": _delta_copies_all(stripped, mg + 8, nxt),
                    }
                )

    header = XtHeader(
        format_kind="binary_delta",
        modeler=modeler,
        schema_name=schema_name,
        common_header=common_meta,
    )
    return DeltaResult(header=header, transactions=txns, remaining_bytes=rem)


def load_xb(path: str | Path, schema: dict[int, dict] | None = None) -> ParseResult:
    """Load and parse a .x_b or .x_p file from disk."""
    data = Path(path).read_bytes()
    return parse_xb(data, schema=schema)
