"""Clean-room binary (.x_b) and text (.x_t) Parasolid XT writers and transcoders."""

from __future__ import annotations

import struct
from typing import Any

from .schema import builtin_schema, effective
from .xb import UNSET_I, UNSET_R, ParseResult, XtHeader, XtNode, parse_xb
from .xt import parse_xt

_LINE_WIDTH = 78


def _build_common_header(format_kind: str, schema_name: str, user_field_size: int) -> bytes:
    lines = [
        "**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**************************",
        '**PARASOLID !"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~0123456789**************************',
        "**PART1;",
        "MC=Unknown;",
        "MC_MODEL=unknown;",
        "MC_ID=unknown;",
        "OS=unknown;",
        "OS_RELEASE=unknown;",
        "FRU=xtopen;",
        "APPL=xtopen;",
        "SITE=unknown;",
        "USER=unknown;",
        f"FORMAT={format_kind};",
        "GUISE=transmit;",
        "KEY=part_file;",
        "FILE=unknown;",
        "DATE=1-jan-2026;",
        "**PART2;",
        f"SCH={schema_name or 'SCH_12000'};",
        f"USFLD_SIZE={user_field_size};",
        "**PART3;",
        "**END_OF_HEADER*****************************************************************",
        "",
    ]
    return ("\n".join(lines)).encode("ascii")


def _extract_nodes_and_header(
    source: ParseResult | list[XtNode],
    header: XtHeader | None,
) -> tuple[list[XtNode], XtHeader, dict[int, list[dict]], dict[int, str]]:
    if isinstance(source, ParseResult):
        return source.nodes, (header or source.header), source.layouts, source.names
    return list(source), (header or XtHeader()), {}, {}


def _is_sch_4part(name: str) -> bool:
    parts = name.split("_")
    return len(parts) == 4 and parts[0] == "SCH" and all(p.isdigit() for p in parts[1:])


def _enc_bin_index(val: int | None) -> bytes:
    if val is None:
        return struct.pack(">h", 0)
    wire = int(val) + 1
    if 0 < wire <= 32767:
        return struct.pack(">h", wire)
    q, r = divmod(wire, 32767)
    if r == 0:
        q -= 1
        r = 32767
    return struct.pack(">hH", -r, q)


def _enc_bin_scalar(code: str, val: Any) -> bytes:
    if code in ("u", "c"):
        return bytes([0 if val is None else (int(val) & 0xFF)])
    if code == "l":
        if val is None:
            return b"\x00"
        return b"\x01" if bool(val) else b"\x00"
    if code == "n":
        return struct.pack(">h", UNSET_I if val is None else int(val))
    if code == "w":
        return struct.pack(">H", 0 if val is None else int(val))
    if code in ("d", "t"):
        return struct.pack(">i", UNSET_I if val is None else int(val))
    if code == "p":
        return _enc_bin_index(val)
    if code == "f":
        return struct.pack(">d", UNSET_R if val is None else float(val))
    if code == "i":
        v = val if isinstance(val, (list, tuple)) else [UNSET_R, UNSET_R]
        return b"".join(_enc_bin_scalar("f", x) for x in v[:2])
    if code in ("v", "h"):
        v = val if isinstance(val, (list, tuple)) else [UNSET_R, UNSET_R, UNSET_R]
        return b"".join(_enc_bin_scalar("f", x) for x in v[:3])
    if code == "b":
        v = val if isinstance(val, (list, tuple)) else [UNSET_R] * 6
        return b"".join(_enc_bin_scalar("f", x) for x in v[:6])
    raise ValueError(f"Unsupported binary scalar code: {code!r}")


def _enc_bin_decl_field(f: dict[str, Any], form2: bool) -> bytes:
    name_b = f["name"].encode("ascii")
    out = bytearray([len(name_b)]) + bytearray(name_b)
    ptr = int(f.get("ptr_class", 0))
    el = int(f.get("elements", 0))
    if el <= 1:
        rem = 0 if el == 0 else 1
        rem_b = struct.pack(">h", rem)
    elif el == 2:
        rem_b = struct.pack(">h", 2)
    elif el < 32767:
        rem_b = struct.pack(">h", el + 1)
    else:
        q, r = divmod(el, 32767)
        rem_b = struct.pack(">hH", -r, q)
    out.extend(struct.pack(">h", ptr))
    out.extend(rem_b)
    if ptr == 0:
        if form2:
            out.append(1 if f.get("transmitted", True) else 0)
            out.append(ord(f["code"]))
        else:
            out.append(1 if f.get("transmitted", True) else 0)
            out.append(ord(f["code"]))
    return bytes(out)


def _ops_against_base(base: list[dict], target: list[dict]) -> list[tuple[str, dict | None]]:
    """Compute minimal Form-1/Form-2 C/D/I/A edit sequence transforming base -> target."""
    if base == target:
        return []
    # Match longest common prefix / aligned base names first
    ops: list[tuple[str, dict | None]] = []
    pos = 0
    base_names = [f["name"] for f in base]
    ti = 0
    while ti < len(target):
        tf = target[ti]
        if pos < len(base) and tf == base[pos]:
            ops.append(("C", None))
            pos += 1
            ti += 1
        elif pos < len(base) and tf["name"] not in base_names[pos:]:
            if pos == len(base):
                ops.append(("A", tf))
            else:
                ops.append(("I", tf))
            ti += 1
        elif pos < len(base):
            ops.append(("D", None))
            pos += 1
        else:
            ops.append(("A", tf))
            ti += 1
    while pos < len(base):
        ops.append(("D", None))
        pos += 1
    return ops


def write_xb(
    source: ParseResult | list[XtNode],
    header: XtHeader | None = None,
    *,
    form2: bool | None = None,
    schema: dict[int, dict] | None = None,
) -> bytes:
    """Serialize XT nodes to binary (.x_b) bytes readable by Parasolid / parse_xb."""
    base_schema = schema if schema is not None else builtin_schema()
    nodes, hdr, src_layouts, src_names = _extract_nodes_and_header(source, header)
    use_f2 = hdr.form2 if form2 is None else form2

    modeler = hdr.modeler or " : TRANSMIT FILE created by xtopen"
    if not modeler.startswith(" : "):
        modeler = f" : {modeler}"
    if form2 is True:
        schema_name = hdr.schema_name if hdr.schema_name.startswith("SCH_") else "SCH_12000_12000_12000"
    elif form2 is False:
        schema_name = "sch_12000" if hdr.schema_name.startswith("SCH_") else (hdr.schema_name or "sch_12000")
    else:
        schema_name = hdr.schema_name or ("SCH_12000_12000_12000" if use_f2 else "sch_12000")
    ufs = hdr.user_field_size or 0

    max_node = hdr.max_node
    if max_node is None and _is_sch_4part(schema_name):
        max_node = max((n.index for n in nodes), default=0) + 1

    buf = bytearray(_build_common_header("binary", schema_name, ufs))
    buf.extend(b"PS\x00\x00")
    mb = modeler.encode("ascii", "replace")
    sb = schema_name.encode("ascii", "replace")
    buf.extend(struct.pack(">h", len(mb)))
    buf.extend(mb)
    buf.extend(struct.pack(">i", len(sb)))
    buf.extend(sb)
    if _is_sch_4part(schema_name):
        buf.extend(struct.pack(">h", int(max_node or 0)))
    buf.extend(struct.pack(">i", int(ufs)))

    seen_layouts: dict[int, list[dict]] = {}
    for n in nodes:
        buf.extend(struct.pack(">h", n.type))
        base_node = base_schema.get(n.type)
        target_layout = src_layouts.get(n.type)
        if target_layout is None and base_node is not None:
            target_layout = effective(base_node)

        if n.type not in seen_layouts:
            if target_layout is None:
                raise ValueError(f"Cannot write unknown XT node type {n.type} without layout")
            if base_node is None:
                nm = src_names.get(n.type, n.name).encode("ascii", "replace")
                buf.append(len(target_layout))
                buf.extend(bytes([len(nm)]) + nm + bytes([len(nm)]) + nm)
                for f in target_layout:
                    buf.extend(_enc_bin_decl_field(f, use_f2))
                if use_f2:
                    buf.append(1)
            else:
                base_eff = effective(base_node)
                ops = _ops_against_base(base_eff, target_layout)
                if not ops:
                    buf.append(255)
                else:
                    buf.append(len(target_layout))
                    for op, fld in ops:
                        buf.extend(op.encode("ascii"))
                        if fld is not None:
                            buf.extend(_enc_bin_decl_field(fld, use_f2))
                    buf.extend(b"Z")
            seen_layouts[n.type] = target_layout

        layout = seen_layouts[n.type]
        is_var = bool(base_node and base_node.get("variable"))
        if is_var:
            vlen = n.varlen
            if vlen is None:
                vlen = _infer_varlen(n, layout)
            buf.extend(struct.pack(">i", int(vlen)))

        buf.extend(_enc_bin_index(n.index))

        fdict = dict(n.fields)
        for f in layout:
            if not f.get("transmitted", True):
                continue
            vals = fdict.get(f["name"], [])
            if f["elements"] == 0 and f["code"] in ("i", "v", "h", "b") and isinstance(vals, (list, tuple)) and vals and not isinstance(vals[0], (list, tuple)):
                vals = [vals]
            elif not isinstance(vals, (list, tuple)):
                vals = [vals]
            if f["code"] == "c":
                buf.extend(bytes(int(x) & 0xFF for x in vals))
            else:
                el = f["elements"]
                if not use_f2 and el == 1 and not is_var:
                    buf.extend(struct.pack(">i", len(vals)))
                for v in vals:
                    buf.extend(_enc_bin_scalar(f["code"], v))

    # Terminator record (type=1, index=0 -> wire 1)
    buf.extend(struct.pack(">hh", 1, 1))
    return bytes(buf)


def _infer_varlen(node: XtNode, layout: list[dict]) -> int:
    fdict = dict(node.fields)
    for f in layout:
        if f["elements"] == 1:
            vals = fdict.get(f["name"], [])
            return len(vals) if isinstance(vals, (list, tuple)) else 1
    return 0


def _fmt_float(x: float | None) -> str:
    if x is None:
        return "?"
    s = f"{float(x):.17g}"
    if s == "-0":
        return "0"
    return s


def _enc_text_scalar(code: str, val: Any) -> str:
    if code in ("u", "n", "w", "d", "t"):
        return "?" if val is None else str(int(val))
    if code == "l":
        if val is None:
            return "?"
        return "T" if bool(val) else "F"
    if code == "p":
        return "0" if val is None else str(int(val))
    if code == "f":
        return _fmt_float(val)
    if code == "i":
        v = val if isinstance(val, (list, tuple)) else [None, None]
        return " ".join(_fmt_float(x) for x in v[:2])
    if code in ("v", "h"):
        v = val if isinstance(val, (list, tuple)) else [None, None, None]
        return " ".join(_fmt_float(x) for x in v[:3])
    if code == "b":
        v = val if isinstance(val, (list, tuple)) else [None] * 6
        return " ".join(_fmt_float(x) for x in v[:6])
    if code == "c":
        if val is None:
            return "?"
        return chr(int(val) & 0xFF)
    raise ValueError(f"Unsupported text scalar code: {code!r}")


def _enc_text_decl_field(f: dict[str, Any]) -> str:
    nm = f["name"]
    ptr = int(f.get("ptr_class", 0))
    el = int(f.get("elements", 0))
    if el <= 2:
        rem_str = str(el)
    elif el < 32767:
        rem_str = str(el)
    else:
        q, r = divmod(el, 32767)
        rem_str = f"{-r} {q}"
    head = f"{len(nm)} {nm}{ptr} {rem_str}"
    if ptr != 0:
        return head + " "
    tr = 1 if f.get("transmitted", True) else 0
    return f"{head} {tr} {f['code']}"


def write_xt(
    source: ParseResult | list[XtNode],
    header: XtHeader | None = None,
    *,
    schema: dict[int, dict] | None = None,
) -> bytes:
    """Serialize XT nodes to text (.x_t) bytes readable by Parasolid / parse_xt."""
    base_schema = schema if schema is not None else builtin_schema()
    nodes, hdr, src_layouts, src_names = _extract_nodes_and_header(source, header)

    modeler = hdr.modeler or "TRANSMIT FILE created by xtopen"
    if not modeler.startswith(" : "):
        modeler = f" : {modeler}"
    schema_name = hdr.schema_name or "SCH_12000_12000_12000"
    ufs = hdr.user_field_size or 0
    max_node = hdr.max_node
    if max_node is None and _is_sch_4part(schema_name):
        max_node = max((n.index for n in nodes), default=0) + 1

    hdr_bytes = _build_common_header("text", schema_name, ufs)
    parts: list[str] = [
        f"T{len(modeler)} {modeler}",
        f"{len(schema_name)} {schema_name}",
    ]
    if _is_sch_4part(schema_name):
        parts.append(f"{int(max_node or 0)} ")
    parts.append(f"{int(ufs)} ")

    seen_layouts: dict[int, list[dict]] = {}
    for n in nodes:
        parts.append(f"{n.type} ")
        base_node = base_schema.get(n.type)
        target_layout = src_layouts.get(n.type)
        if target_layout is None and base_node is not None:
            target_layout = effective(base_node)

        if n.type not in seen_layouts:
            if target_layout is None:
                raise ValueError(f"Cannot write unknown XT node type {n.type} without layout")
            if base_node is None:
                nm = src_names.get(n.type, n.name)
                parts.append(f"{len(target_layout)} {len(nm)} {nm}{len(nm)} {nm}")
                for f in target_layout:
                    parts.append(_enc_text_decl_field(f))
            else:
                base_eff = effective(base_node)
                ops = _ops_against_base(base_eff, target_layout)
                if not ops:
                    parts.append("255 ")
                else:
                    parts.append(f"{len(target_layout)} ")
                    for op, fld in ops:
                        parts.append(op)
                        if fld is not None:
                            parts.append(_enc_text_decl_field(fld))
                    parts.append("Z")
            seen_layouts[n.type] = target_layout

        layout = seen_layouts[n.type]
        is_var = bool(base_node and base_node.get("variable"))
        if is_var:
            vlen = n.varlen if n.varlen is not None else _infer_varlen(n, layout)
            parts.append(f"{int(vlen)} ")

        parts.append(f"{n.index} ")

        fdict = dict(n.fields)
        prev_was_glued = False
        for f in layout:
            if not f.get("transmitted", True):
                continue
            vals = fdict.get(f["name"], [])
            if f["elements"] == 0 and f["code"] in ("i", "v", "h", "b") and isinstance(vals, (list, tuple)) and vals and not isinstance(vals[0], (list, tuple)):
                vals = [vals]
            elif not isinstance(vals, (list, tuple)):
                vals = [vals]
            el = f["elements"]
            code = f["code"]

            if code == "c" and el == 1:
                raw_s = "".join(chr(int(x) & 0xFF) for x in vals)
                parts.append(raw_s)
                prev_was_glued = True
                continue

            if not hdr.form2 and el == 1 and not is_var:
                parts.append(f"{len(vals)} ")

            for v in vals:
                tok = _enc_text_scalar(code, v)
                if code in ("c", "l") and tok in ("T", "F", "+", "-", "?"):
                    parts.append(tok)
                    prev_was_glued = True
                else:
                    if prev_was_glued:
                        parts.append(tok + " ")
                        prev_was_glued = False
                    else:
                        parts.append(tok + " ")
                        prev_was_glued = False

    parts.append("1 0 ")
    raw_stream = "".join(parts)
    wrapped = "\n".join(
        raw_stream[i : i + _LINE_WIDTH] for i in range(0, len(raw_stream), _LINE_WIDTH)
    )
    return hdr_bytes + (wrapped + "\n").encode("latin-1")


def xb_to_xt(data: bytes, schema: dict[int, dict] | None = None) -> bytes:
    """Convert binary .x_b bytes directly into text .x_t bytes."""
    res = parse_xb(data, schema=schema)
    return write_xt(res, schema=schema)


def xt_to_xb(
    data: bytes | str,
    schema: dict[int, dict] | None = None,
    *,
    form2: bool | None = None,
) -> bytes:
    """Convert text .x_t bytes/string directly into binary .x_b bytes."""
    res = parse_xt(data, schema=schema)
    return write_xb(res, form2=form2, schema=schema)
