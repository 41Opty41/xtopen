"""Clean-room text Parasolid XT (.x_t) parser.

Implements a re-entrant, zero-global XtParser supporting Form-1 and Form-2
text transmit streams with inline schema edit declarations (C/D/I/A/Z) and
built-in schema defaults.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .schema import builtin_schema, effective
from .xb import ParseResult, XtHeader, XtNode, _parse_common_header

_INT_RE = re.compile(r"[+-]?\d+")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


class _TextCur:
    """Character cursor over an unwrapped XT text stream."""

    def __init__(self, text: str, pos: int = 0) -> None:
        self.s = text
        self.p = pos
        self.n = len(text)

    def skip_ws(self) -> None:
        while self.p < self.n and self.s[self.p] in " \t":
            self.p += 1

    def peek_nonws(self) -> str:
        i = self.p
        while i < self.n and self.s[i] in " \t":
            i += 1
        return self.s[i] if i < self.n else ""

    def read_char(self) -> str:
        if self.p >= self.n:
            raise EOFError("Unexpected end of XT text stream")
        ch = self.s[self.p]
        self.p += 1
        return ch

    def read_nonws_char(self) -> str:
        self.skip_ws()
        return self.read_char()

    def read_int(self) -> int | None:
        self.skip_ws()
        if self.p >= self.n:
            raise EOFError("Unexpected EOF reading integer")
        if self.s[self.p] == "?":
            self.p += 1
            return None
        m = _INT_RE.match(self.s, self.p)
        if m is None:
            raise ValueError(f"Expected integer at offset {self.p}, got {self.s[self.p : self.p + 20]!r}")
        self.p = m.end()
        return int(m.group(0))

    def read_float(self) -> float | None:
        self.skip_ws()
        if self.p >= self.n:
            raise EOFError("Unexpected EOF reading float")
        if self.s[self.p] == "?":
            self.p += 1
            return None
        m = _FLOAT_RE.match(self.s, self.p)
        if m is None:
            raise ValueError(f"Expected float at offset {self.p}, got {self.s[self.p : self.p + 20]!r}")
        self.p = m.end()
        return float(m.group(0))

    def read_logical(self) -> int | None:
        self.skip_ws()
        if self.p >= self.n:
            raise EOFError("Unexpected EOF reading logical")
        ch = self.s[self.p]
        if ch in ("T", "F"):
            self.p += 1
            return 1 if ch == "T" else 0
        if ch == "?":
            self.p += 1
            return None
        if ch in ("0", "1"):
            v = self.read_int()
            return 1 if v else 0
        raise ValueError(f"Expected logical at offset {self.p}, got {ch!r}")

    def read_short_string(self) -> str:
        length = self.read_int()
        if length is None or length < 0:
            return ""
        # XT text short_string has exactly one separator char (space) before N chars
        if self.p < self.n and self.s[self.p] == " ":
            self.p += 1
        if self.p + length > self.n:
            raise EOFError(f"Unexpected EOF reading string of length {length}")
        out = self.s[self.p : self.p + length]
        self.p += length
        return out


def _decode_xt_elements(rem: int, q_fn) -> int:
    if rem < 0:
        q = q_fn() or 0
        return q * 32767 + (-rem)
    if rem == 0:
        return 0
    return rem


class XtParser:
    """Re-entrant parser for Parasolid XT text transmit (.x_t) streams."""

    def __init__(self, schema: dict[int, dict] | None = None) -> None:
        self.base_schema = schema if schema is not None else builtin_schema()

    def _read_decl_field(self, cur: _TextCur, form2: bool) -> dict[str, Any]:
        name = cur.read_short_string()
        ptr = cur.read_int()
        if ptr is None:
            ptr = 0
        rem = cur.read_int()
        if rem is None:
            rem = 0
        el = _decode_xt_elements(rem, cur.read_int)
        if ptr != 0:
            code = "p"
            transmitted = True
        else:
            trans_val = cur.read_int()
            transmitted = bool(trans_val)
            code = cur.read_nonws_char()
        return {
            "name": name,
            "code": code,
            "transmitted": transmitted,
            "ptr_class": ptr,
            "elements": el,
            "rec": form2,
        }

    def _read_scalar(self, cur: _TextCur, code: str) -> Any:
        if code in ("u", "n", "w", "d", "t"):
            return cur.read_int()
        if code == "l":
            return cur.read_logical()
        if code == "p":
            idx = cur.read_int()
            return None if idx in (None, 0) else idx
        if code == "f":
            return cur.read_float()
        if code == "i":
            return [cur.read_float(), cur.read_float()]
        if code in ("v", "h"):
            return [cur.read_float(), cur.read_float(), cur.read_float()]
        if code == "b":
            return [cur.read_float() for _ in range(6)]
        if code == "c":
            ch = cur.read_nonws_char()
            if ch == "?":
                return None
            return ord(ch)
        raise ValueError(f"Unsupported XT text field code: {code!r}")

    def parse(self, data: bytes | str) -> ParseResult:
        """Decode an .x_t text transmit payload into a ParseResult."""
        if isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
        else:
            raw = data.encode("latin-1")

        common_meta: dict[str, str] = {}
        body_bytes = raw
        if raw.lstrip(b" \t\r\n").startswith(b"**"):
            i = raw.find(b"**END_OF_HEADER")
            if i < 0:
                raise ValueError("Missing **END_OF_HEADER marker in .x_t header")
            j = raw.find(b"\n", i)
            if j < 0:
                raise ValueError("Malformed **END_OF_HEADER line")
            common_meta = _parse_common_header(raw[:i])
            body_bytes = raw[j + 1 :]

        stream = body_bytes.replace(b"\r", b"").replace(b"\n", b"").decode("latin-1")
        cur = _TextCur(stream, 0)
        cur.skip_ws()
        tag = cur.read_char()
        if tag != "T":
            raise ValueError(f"Expected 'T' preamble in .x_t stream, got {tag!r}")

        modeler = cur.read_short_string().strip()
        schema_name = cur.read_short_string().strip()
        parts = schema_name.split("_")
        max_node: int | None = None
        if len(parts) == 4 and parts[0] == "SCH" and all(p.isdigit() for p in parts[1:]):
            max_node = cur.read_int()
        ufs = cur.read_int() or 0
        form2 = schema_name.startswith("SCH_")

        layouts: dict[int, list[dict]] = {}
        names: dict[int, str] = {}
        ext_types: set[int] = set()
        nodes: list[XtNode] = []

        while True:
            cur.skip_ws()
            if cur.p >= cur.n:
                break
            rec_start = cur.p
            ntype = cur.read_int()
            if ntype is None:
                raise ValueError(f"Unset node type at text offset {rec_start}")
            if ntype == 1:
                _ = cur.read_int()  # terminator index
                break

            node = self.base_schema.get(ntype)
            if ntype not in layouts:
                flag = cur.read_int()
                if flag is None:
                    raise ValueError(f"Unset layout flag for type {ntype} at {rec_start}")
                if node is None:
                    if flag == 255:
                        raise ValueError(f"Unknown node type {ntype} with flag 255 at {rec_start}")
                    nm = cur.read_short_string()
                    _desc = cur.read_short_string()
                    layout = [self._read_decl_field(cur, form2) for _ in range(flag)]
                    if form2:
                        layout = [f for f in layout if f["name"] != "node_id"]
                    layouts[ntype] = layout
                    names[ntype] = nm
                    ext_types.add(ntype)
                    node = {"type": ntype, "name": nm, "variable": form2, "fields": layout}
                elif flag == 255:
                    layouts[ntype] = effective(node)
                else:
                    base = effective(node)
                    nxt = cur.peek_nonws()
                    if nxt in ("C", "D", "I", "A", "Z"):
                        out: list[dict] = []
                        pos = 0
                        while True:
                            op = cur.read_nonws_char()
                            if op == "C":
                                out.append(base[pos])
                                pos += 1
                            elif op == "D":
                                pos += 1
                            elif op in ("I", "A"):
                                out.append(self._read_decl_field(cur, form2))
                            elif op == "Z":
                                break
                            else:
                                raise ValueError(f"Unknown text schema op {op!r} at {cur.p}")
                        layouts[ntype] = out
                    elif form2 and flag == 1:
                        layouts[ntype] = base
                    else:
                        nm = cur.read_short_string()
                        _desc = cur.read_short_string()
                        names[ntype] = nm
                        layouts[ntype] = [self._read_decl_field(cur, form2) for _ in range(flag)]
                        ext_types.add(ntype)
                        node = {"type": ntype, "name": nm, "variable": True, "fields": layouts[ntype]}

            layout = layouts[ntype]
            if node is None:
                node = {
                    "type": ntype,
                    "name": names.get(ntype, f"type_{ntype}"),
                    "variable": ntype in ext_types,
                    "fields": layout,
                }

            varlen: int | None = None
            if ntype in ext_types or node.get("variable"):
                varlen = cur.read_int()

            fidx = cur.read_int()
            if fidx is None or fidx == 0:
                raise ValueError(f"Invalid node index {fidx} at text offset {rec_start}")

            fields: list[tuple[str, Any]] = []
            for f in layout:
                if not f.get("transmitted", True):
                    continue
                el = f["elements"]
                if el == 0:
                    count = 1
                elif form2 and f.get("rec"):
                    count = (varlen if varlen is not None else 2) if el == 2 else el
                elif el == 1:
                    if varlen is not None:
                        count = varlen
                    elif form2:
                        count = 1
                    else:
                        count = cur.read_int() or 0
                elif el == 2:
                    count = 2 if form2 else (cur.read_int() or 0)
                else:
                    count = el

                if f["code"] == "c" and el == 1:
                    # Variable-length character sequence starts after 1 space
                    if cur.p < cur.n and cur.s[cur.p] == " ":
                        cur.p += 1
                    if cur.p + count > cur.n:
                        raise EOFError(f"Unexpected EOF reading {count} chars at {cur.p}")
                    raw_chars = cur.s[cur.p : cur.p + count]
                    cur.p += count
                    vals: Any = [ord(ch) for ch in raw_chars]
                elif f["code"] == "c":
                    vals = [self._read_scalar(cur, "c") for _ in range(count)]
                else:
                    vals = [self._read_scalar(cur, f["code"]) for _ in range(count)]
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

        cur.skip_ws()
        header = XtHeader(
            format_kind="text",
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
            remaining_bytes=max(0, cur.n - cur.p),
            layouts=layouts,
            names=names,
        )


def parse_xt(data: bytes | str, schema: dict[int, dict] | None = None) -> ParseResult:
    """Parse Parasolid text transmit (.x_t) bytes or string into ParseResult."""
    return XtParser(schema=schema).parse(data)


def load_xt(path: str | Path, schema: dict[int, dict] | None = None) -> ParseResult:
    """Load and parse an .x_t text file from disk."""
    return parse_xt(Path(path).read_bytes(), schema=schema)
