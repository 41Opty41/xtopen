"""Clean-room Siemens NX (.prt) container reader and Parasolid XT stream extractor.

Supports:
- Modern NX ``SPLMSSTR`` compound container files (zlib-compressed & raw XT records).
- Legacy NX ``OLE2`` / Microsoft Compound File Binary (MS-CFB) container files.
- Structured ``0xEDF1D1C0`` Parasolid chunk framing + safe fallback scanning inside ``UG_PART``.
- Multi-stream deduplication and ``BrepModel`` merging, plus optional JPEG preview extraction.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
import re
import struct
from typing import Any, Sequence
import zlib

from .model import (
    AssemblyInfo,
    Attribute,
    BrepModel,
    Coedge,
    Edge,
    Face,
    Loop,
    NurbsCurve,
    Vertex,
    extract_brep,
)
from .xb import ParseResult, XtHeader, parse_delta, parse_xb

SPLMSSTR_MAGIC = b"SPLMSSTR"
OLE2_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
_PS_RECORD_MAGIC = b"\xC0\xD1\xF1\xED"
_ZLIB_HEADER_SECOND = (0x01, 0x5E, 0x9C, 0xDA)
_FREESECT = 0xFFFFFFFC


@dataclass
class PrtStream:
    """Single Parasolid transmit stream extracted from a NX ``.prt`` ``UG_PART`` payload."""

    index: int
    kind: str  # "body", "partition", or "delta"
    offset: int
    compressed: bool
    raw_xb: bytes
    ps_bytes: bytes
    ascii_header: bytes = b""
    schema_name: str = ""
    body_count: int = 0
    face_count: int = 0


@dataclass
class PrtArchive:
    """Decoded NX ``.prt`` container holding extracted Parasolid streams and optional metadata."""

    container: str  # "splmsstr" or "ole2"
    streams: list[PrtStream] = field(default_factory=list)
    preview: bytes | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    entry_names: list[str] = field(default_factory=list)


def is_prt(data: bytes) -> bool:
    """Return ``True`` if ``data`` begins with an NX ``SPLMSSTR`` or ``OLE2`` container header."""
    return data.startswith(SPLMSSTR_MAGIC) or data.startswith(OLE2_MAGIC)


def parse_splmsstr(data: bytes) -> dict[str, bytes]:
    """Parse an NX ``SPLMSSTR`` archive and return ``{path: stream_bytes}``."""
    if len(data) < 35 or not data.startswith(SPLMSSTR_MAGIC):
        raise ValueError("Not a valid SPLMSSTR container")
    if data[25:31] != b"HEADER":
        raise ValueError("Missing SPLMSSTR directory HEADER marker")

    entry_count = struct.unpack_from("<I", data, 31)[0]
    pos = 35
    entries: dict[str, bytes] = {}
    for _ in range(entry_count):
        if pos + 4 > len(data):
            raise ValueError("Truncated SPLMSSTR directory entry")
        path_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if pos + path_len > len(data):
            raise ValueError("Truncated SPLMSSTR entry path")
        path = data[pos : pos + path_len].decode("ascii", "replace")
        pos += path_len
        if path.endswith("/"):
            if pos + 16 > len(data):
                raise ValueError("Truncated SPLMSSTR directory GUID")
            pos += 16
        else:
            if pos + 16 > len(data):
                raise ValueError("Truncated SPLMSSTR stream span")
            off, size = struct.unpack_from("<QQ", data, pos)
            pos += 16
            if off > len(data) or off + size > len(data):
                raise ValueError(f"SPLMSSTR entry {path!r} out of bounds")
            entries[path] = data[off : off + size]
    return entries


def parse_ole2_cfb(data: bytes) -> dict[str, bytes]:
    """Parse a Microsoft Compound File Binary (OLE2) container into ``{name: stream_bytes}``."""
    if len(data) < 512 or not data.startswith(OLE2_MAGIC):
        raise ValueError("Not a valid OLE2 Compound File Binary container")

    sec_shift, mini_shift = struct.unpack_from("<HH", data, 30)
    if sec_shift < 7 or sec_shift > 16 or mini_shift > sec_shift:
        raise ValueError("Invalid OLE2 sector shift values")
    sec_size = 1 << sec_shift
    mini_size = 1 << mini_shift

    dir_start = struct.unpack_from("<I", data, 48)[0]
    mini_cutoff = struct.unpack_from("<I", data, 56)[0]
    minifat_start = struct.unpack_from("<I", data, 60)[0]
    difat_start = struct.unpack_from("<I", data, 68)[0]
    num_difat_sectors = struct.unpack_from("<I", data, 72)[0]

    fat_sectors: list[int] = [s for s in struct.unpack_from("<109I", data, 76) if s < _FREESECT]

    cur_difat = difat_start
    seen_difat: set[int] = set()
    ints_per_sec = sec_size // 4
    while cur_difat < _FREESECT and len(seen_difat) < max(num_difat_sectors, 1) + 16:
        if cur_difat in seen_difat:
            break
        seen_difat.add(cur_difat)
        off = (cur_difat + 1) * sec_size
        if off + sec_size > len(data):
            break
        entries = struct.unpack_from(f"<{ints_per_sec}I", data, off)
        fat_sectors.extend(s for s in entries[:-1] if s < _FREESECT)
        cur_difat = entries[-1]

    fat: list[int] = []
    for s in fat_sectors:
        off = (s + 1) * sec_size
        if off + sec_size > len(data):
            break
        fat.extend(struct.unpack_from(f"<{ints_per_sec}I", data, off))

    def _read_chain(start: int, max_len: int | None = None) -> bytes:
        out = bytearray()
        s = start
        seen: set[int] = set()
        while s < _FREESECT and s not in seen and s < len(fat):
            seen.add(s)
            off = (s + 1) * sec_size
            if off + sec_size > len(data):
                break
            out.extend(data[off : off + sec_size])
            if max_len is not None and len(out) >= max_len:
                break
            s = fat[s]
        return bytes(out[:max_len]) if max_len is not None else bytes(out)

    dir_raw = _read_chain(dir_start)
    if len(dir_raw) < 128:
        raise ValueError("Empty or corrupt OLE2 directory stream")

    root_start, root_size = struct.unpack_from("<IQ", dir_raw, 116)
    if sec_size == 512:
        root_size &= 0xFFFFFFFF
    ministream = _read_chain(root_start, root_size) if root_start < _FREESECT and root_size > 0 else b""

    minifat: list[int] = []
    if minifat_start < _FREESECT:
        mf_raw = _read_chain(minifat_start)
        if mf_raw:
            minifat = list(struct.unpack(f"<{len(mf_raw) // 4}I", mf_raw))

    def _read_mini(start: int, sz: int) -> bytes:
        out = bytearray()
        s = start
        seen: set[int] = set()
        while s < _FREESECT and len(out) < sz and s not in seen and s < len(minifat):
            seen.add(s)
            off = s * mini_size
            if off + mini_size > len(ministream):
                out.extend(ministream[off : min(len(ministream), off + mini_size)])
                break
            out.extend(ministream[off : off + mini_size])
            s = minifat[s]
        return bytes(out[:sz])

    streams: dict[str, bytes] = {}
    for i in range(0, len(dir_raw) - 127, 128):
        ent = dir_raw[i : i + 128]
        name_len = struct.unpack_from("<H", ent, 64)[0]
        obj_type = ent[66]
        if obj_type != 2 or name_len < 2 or name_len > 64:
            continue
        name = ent[: name_len - 2].decode("utf-16le", "replace")
        start, raw_sz = struct.unpack_from("<IQ", ent, 116)
        size = raw_sz & 0xFFFFFFFF if sec_size == 512 else raw_sz
        if size == 0:
            streams[name] = b""
        elif size < mini_cutoff:
            streams[name] = _read_mini(start, size)
        else:
            streams[name] = _read_chain(start, size)
    return streams


def _clean_ascii_header(hdr: bytes) -> bytes:
    clean = hdr.rstrip(b"+ \t\r\n")
    if not clean:
        return b""
    return clean + b"\n"


def _strip_leading_plus_ps(buf: bytes) -> bytes:
    if buf.startswith(b"+PS\x00\x00"):
        return buf[1:]
    return buf


def _make_standalone_xb(ascii_hdr: bytes, ps_bytes: bytes) -> bytes:
    if ascii_hdr.startswith(b"**ABC"):
        cleaned = _clean_ascii_header(ascii_hdr)
        if cleaned:
            return cleaned + ps_bytes
    return ps_bytes


def _classify_stream(
    idx: int,
    offset: int,
    compressed: bool,
    ascii_hdr: bytes,
    ps_bytes: bytes,
) -> PrtStream | None:
    standalone = _make_standalone_xb(ascii_hdr, ps_bytes)
    try:
        parsed = parse_xb(standalone)
        body_count = sum(1 for n in parsed.nodes if n.type == 12)
        face_count = sum(1 for n in parsed.nodes if n.type == 14)
        root_name = parsed.nodes[0].name if parsed.nodes else ""
        kind = "partition" if root_name == "WORLD" else "body"
        return PrtStream(
            index=idx,
            kind=kind,
            offset=offset,
            compressed=compressed,
            raw_xb=standalone,
            ps_bytes=ps_bytes,
            ascii_header=ascii_hdr,
            schema_name=parsed.header.schema_name,
            body_count=body_count,
            face_count=face_count,
        )
    except Exception:
        pass

    try:
        dr = parse_delta(standalone)
        return PrtStream(
            index=idx,
            kind="delta",
            offset=offset,
            compressed=compressed,
            raw_xb=standalone,
            ps_bytes=ps_bytes,
            ascii_header=ascii_hdr,
            schema_name=dr.header.schema_name,
        )
    except Exception:
        return None


def _scan_ug_part_structured(ug: bytes) -> list[PrtStream]:
    streams: list[PrtStream] = []
    n = len(ug)
    pos = 0
    while True:
        off = ug.find(_PS_RECORD_MAGIC, pos)
        if off == -1 or off + 12 > n:
            break
        flags, payload_len = struct.unpack_from("<II", ug, off + 4)
        if flags in (0xFFFFFFFF, 0xEDF1D1C0) or payload_len == 0 or payload_len > n:
            pos = off + 4
            continue

        is_zlib = (flags & 0x8000_0000) != 0
        has_ext = (flags & 0x4000_0000) != 0
        ascii_len = flags & 0x3FFF_FFFF
        if ascii_len > 65536:
            pos = off + 4
            continue

        hdr_start: int | None = None
        if has_ext and off + 16 <= n:
            ext_len = struct.unpack_from("<I", ug, off + 12)[0]
            if 16 <= ext_len <= 4096:
                for cand in (off + ext_len, off + ext_len + 4):
                    pstart = cand + ascii_len
                    if pstart + payload_len > n:
                        continue
                    if ascii_len > 0:
                        if ug[cand : cand + 5] == b"**ABC":
                            hdr_start = cand
                            break
                    else:
                        if is_zlib:
                            if ug[pstart] == 0x78 and ug[pstart + 1] in _ZLIB_HEADER_SECOND:
                                hdr_start = cand
                                break
                        else:
                            if ug[pstart : pstart + 4] == b"PS\x00\x00" or ug[pstart : pstart + 5] == b"+PS\x00\x00":
                                hdr_start = cand
                                break
        else:
            cand = off + 12
            pstart = cand + ascii_len
            if pstart + payload_len <= n:
                if ascii_len > 0 and ug[cand : cand + 5] == b"**ABC":
                    hdr_start = cand
                elif ascii_len == 0:
                    if is_zlib and pstart + 2 <= n and ug[pstart] == 0x78 and ug[pstart + 1] in _ZLIB_HEADER_SECOND:
                        hdr_start = cand
                    elif not is_zlib and (
                        ug[pstart : pstart + 4] == b"PS\x00\x00" or ug[pstart : pstart + 5] == b"+PS\x00\x00"
                    ):
                        hdr_start = cand

        if hdr_start is None:
            pos = off + 4
            continue

        payload_start = hdr_start + ascii_len
        ascii_hdr = ug[hdr_start:payload_start] if ascii_len > 0 else b""
        raw_slice = ug[payload_start : payload_start + payload_len]

        if is_zlib:
            try:
                decompressed = zlib.decompress(raw_slice)
            except Exception:
                pos = off + 4
                continue
        else:
            decompressed = raw_slice

        ps_pos = decompressed.find(b"PS\x00\x00", 0, min(len(decompressed), 4096))
        if ps_pos == -1:
            pos = off + 4
            continue
        if not ascii_hdr and ps_pos > 0:
            ascii_hdr = decompressed[:ps_pos]
        ps_bytes = decompressed[ps_pos:]
        if not ps_bytes.startswith(b"PS\x00\x00"):
            pos = off + 4
            continue

        classified = _classify_stream(len(streams), off, is_zlib, ascii_hdr, ps_bytes)
        if classified is not None:
            streams.append(classified)
            pos = payload_start + payload_len
        else:
            pos = off + 4

    return streams


def _scan_ug_part_fallback(ug: bytes) -> list[PrtStream]:
    streams: list[PrtStream] = []
    n = len(ug)
    i = 0
    while i < n - 2:
        if ug[i] == 0x78 and ug[i + 1] in _ZLIB_HEADER_SECOND:
            try:
                d = zlib.decompressobj()
                out = d.decompress(ug[i:]) + d.flush()
                consumed = n - i - len(d.unused_data)
                if consumed >= 8 and len(out) >= 16:
                    ps_pos = out.find(b"PS\x00\x00", 0, min(len(out), 4096))
                    if ps_pos != -1:
                        ascii_hdr = out[:ps_pos] if ps_pos > 0 else b""
                        ps_bytes = out[ps_pos:]
                        classified = _classify_stream(len(streams), i, True, ascii_hdr, ps_bytes)
                        if classified is not None:
                            streams.append(classified)
                            i += consumed
                            continue
            except Exception:
                pass
        i += 1

    if streams:
        return streams

    # Raw uncompressed PS\x00\x00 fallback
    ps_offsets = [m.start() for m in re.finditer(rb"PS\x00\x00", ug)]
    for idx, p_off in enumerate(ps_offsets):
        end_off = ps_offsets[idx + 1] if idx + 1 < len(ps_offsets) else n
        hdr_slice = ug[max(0, p_off - 1024) : p_off]
        abc_pos = hdr_slice.rfind(b"**ABC")
        ascii_hdr = hdr_slice[abc_pos:] if abc_pos != -1 else b""
        ps_bytes = ug[p_off:end_off]
        classified = _classify_stream(len(streams), p_off, False, ascii_hdr, ps_bytes)
        if classified is not None:
            streams.append(classified)
    return streams


def extract_prt(data: bytes) -> PrtArchive:
    """Extract Parasolid XT streams, preview image, and metadata from an NX ``.prt`` byte buffer."""
    if data.startswith(SPLMSSTR_MAGIC):
        container = "splmsstr"
        entries = parse_splmsstr(data)
        ug_part = entries.get("/Root/UG_PART/UG_PART")
        if ug_part is None:
            for k, v in entries.items():
                if k.upper().endswith("/UG_PART"):
                    ug_part = v
                    break
        preview = entries.get("/Root/images/preview")
        metadata: dict[str, str] = {}
        for meta_key in ("/Root/part/attrs", "/Root/qafmetadata"):
            if meta_key in entries:
                metadata[meta_key] = entries[meta_key].decode("utf-8", "replace")
    elif data.startswith(OLE2_MAGIC):
        container = "ole2"
        entries = parse_ole2_cfb(data)
        ug_part = entries.get("UG_PART")
        if ug_part is None:
            for k, v in entries.items():
                if k.upper() == "UG_PART":
                    ug_part = v
                    break
        preview = entries.get("preview")
        metadata = {}
        for meta_key in ("attrs", "qafmetadata"):
            if meta_key in entries:
                metadata[meta_key] = entries[meta_key].decode("utf-8", "replace")
    else:
        raise ValueError("Unsupported file format: expected NX SPLMSSTR or OLE2/CFB .prt container")

    if ug_part is None:
        raise ValueError(f"NX .prt ({container}) container does not contain a UG_PART stream")

    streams = _scan_ug_part_structured(ug_part)
    if not streams:
        streams = _scan_ug_part_fallback(ug_part)

    return PrtArchive(
        container=container,
        streams=streams,
        preview=preview,
        metadata=metadata,
        entry_names=sorted(entries.keys()),
    )


def load_prt_archive(path: str | Path) -> PrtArchive:
    """Load and decode an NX ``.prt`` file from disk into a :class:`PrtArchive`."""
    return extract_prt(Path(path).read_bytes())


def _select_brep_streams(archive: PrtArchive, stream_index: int | None = None) -> list[PrtStream]:
    if stream_index is not None:
        if stream_index < 0 or stream_index >= len(archive.streams):
            raise ValueError(
                f"Requested stream_index={stream_index}, but archive has {len(archive.streams)} stream(s)"
            )
        chosen = archive.streams[stream_index]
        if chosen.kind == "delta":
            raise ValueError(f"Stream #{stream_index} is a delta transaction stream, not a B-rep body")
        return [chosen]

    seen_ps: set[bytes] = set()
    unique: list[PrtStream] = []
    for st in archive.streams:
        if st.kind == "delta":
            continue
        if st.ps_bytes in seen_ps:
            continue
        seen_ps.add(st.ps_bytes)
        unique.append(st)

    if not unique:
        raise ValueError(
            f"NX .prt ({archive.container}) contains no embedded Parasolid B-rep bodies "
            f"(found {len(archive.streams)} stream(s); legacy pre-V13 internal storage or assembly-only part)"
        )
    return unique


def _bump(val: int | None, off: int) -> int | None:
    if val is None or val <= 0:
        return val
    return val + off


def _max_model_id(model: BrepModel) -> int:
    hi = 0
    for d in (model.vertices, model.edges, model.faces, model.surfaces, model.curves):
        if d:
            hi = max(hi, max(d.keys()))
    for b in model.bodies:
        hi = max(hi, int(b.get("index") or 0), *(int(s) for s in b.get("shell_indices", ())))
    for a in model.assemblies:
        hi = max(hi, a.index, *(int(i) for i in a.instance_indices))
    for ins in model.instances:
        hi = max(hi, int(ins.get("index") or 0))
    for at in model.attributes:
        hi = max(hi, at.index)
    for f in model.faces.values():
        for lp in f.loops:
            hi = max(hi, lp.index)
            for ce in lp.coedges:
                hi = max(hi, ce.fin_index)
    return hi


def _remap_surface_dict(s: dict[str, Any], off: int) -> dict[str, Any]:
    out = copy.deepcopy(s)
    if "profile_index" in out:
        out["profile_index"] = _bump(out["profile_index"], off)
    if "profile" in out and isinstance(out["profile"], dict):
        out["profile"] = _remap_curve_dict(out["profile"], off)
    if "base_surface_index" in out:
        out["base_surface_index"] = _bump(out["base_surface_index"], off)
    if "base_surface" in out and isinstance(out["base_surface"], dict):
        out["base_surface"] = _remap_surface_dict(out["base_surface"], off)
    return out


def _remap_curve_dict(c: dict[str, Any], off: int) -> dict[str, Any]:
    out = copy.deepcopy(c)
    if "sp_surface_index" in out:
        out["sp_surface_index"] = _bump(out["sp_surface_index"], off)
    if "sp_surface" in out and isinstance(out["sp_surface"], dict):
        out["sp_surface"] = _remap_surface_dict(out["sp_surface"], off)
    if "surface_indices" in out and isinstance(out["surface_indices"], list):
        out["surface_indices"] = [_bump(x, off) for x in out["surface_indices"]]
    if "chart" in out:
        out["chart"] = _bump(out["chart"], off)
    nurbs = out.get("nurbs")
    if isinstance(nurbs, NurbsCurve) and nurbs.host_surface_index is not None:
        nurbs.host_surface_index = _bump(nurbs.host_surface_index, off)
    return out


def merge_brep_models(models: list[BrepModel], *, header: XtHeader | None = None) -> BrepModel:
    """Merge multiple extracted :class:`BrepModel` objects with disjoint topological/entity IDs."""
    if not models:
        raise ValueError("Cannot merge an empty list of BrepModels")
    if len(models) == 1:
        m = models[0]
        if header is not None:
            m.header = header
        return m

    merged = BrepModel(
        header=header if header is not None else models[0].header,
        bodies=[],
        vertices={},
        edges={},
        faces={},
        surfaces={},
        curves={},
        attributes=[],
        instances=[],
        assemblies=[],
    )

    next_offset = 0
    for idx, src in enumerate(models):
        off = next_offset

        for vid, v in src.vertices.items():
            nv = _bump(vid, off)
            assert nv is not None
            merged.vertices[nv] = Vertex(
                index=nv,
                node_id=v.node_id,
                point=v.point,
                tolerance=v.tolerance,
            )

        for sid, sdict in src.surfaces.items():
            ns = _bump(sid, off)
            assert ns is not None
            merged.surfaces[ns] = _remap_surface_dict(sdict, off)

        for cid, cdict in src.curves.items():
            nc = _bump(cid, off)
            assert nc is not None
            merged.curves[nc] = _remap_curve_dict(cdict, off)

        for eid, e in src.edges.items():
            ne = _bump(eid, off)
            assert ne is not None
            merged.edges[ne] = Edge(
                index=ne,
                node_id=e.node_id,
                curve_type=e.curve_type,
                sense=e.sense,
                tolerance=e.tolerance,
                curve_index=_bump(e.curve_index, off),
                curve=_remap_curve_dict(e.curve, off),
            )

        for fid, f in src.faces.items():
            nf = _bump(fid, off)
            assert nf is not None
            new_loops: list[Loop] = []
            for lp in f.loops:
                nlp = _bump(lp.index, off)
                assert nlp is not None
                new_coedges = [
                    Coedge(
                        edge_index=_bump(ce.edge_index, off) or ce.edge_index,
                        sense=ce.sense,
                        start_vertex=_bump(ce.start_vertex, off),
                        end_vertex=_bump(ce.end_vertex, off),
                        fin_index=_bump(ce.fin_index, off) or ce.fin_index,
                    )
                    for ce in lp.coedges
                ]
                new_loops.append(Loop(index=nlp, coedges=new_coedges))

            new_f_attrs = [
                Attribute(
                    index=_bump(at.index, off) or at.index,
                    name=at.name,
                    owner_index=_bump(at.owner_index, off),
                    values=copy.deepcopy(at.values),
                )
                for at in f.attributes
            ]
            merged.faces[nf] = Face(
                index=nf,
                node_id=f.node_id,
                shell_index=_bump(f.shell_index, off),
                sense=f.sense,
                face_sense=f.face_sense,
                surface_sense=f.surface_sense,
                surface_type=f.surface_type,
                surface_index=_bump(f.surface_index, off),
                surface=_remap_surface_dict(f.surface, off),
                loops=new_loops,
                tolerance=f.tolerance,
                attributes=new_f_attrs,
                appearance=copy.deepcopy(f.appearance),
            )

        for b in src.bodies:
            nb = copy.deepcopy(b)
            nb["index"] = _bump(b.get("index"), off)
            nb["shell_indices"] = [_bump(s, off) for s in b.get("shell_indices", ())]
            if not nb.get("name"):
                nb["name"] = f"Part_{idx + 1}"
            merged.bodies.append(nb)

        for ins in src.instances:
            ni = copy.deepcopy(ins)
            ni["index"] = _bump(ins.get("index"), off)
            ni["part"] = _bump(ins.get("part"), off)
            ni["assembly"] = _bump(ins.get("assembly"), off)
            merged.instances.append(ni)

        for asm in src.assemblies:
            na = AssemblyInfo(
                index=_bump(asm.index, off) or asm.index,
                node_id=asm.node_id,
                name=asm.name,
                appearance=copy.deepcopy(asm.appearance),
                instance_indices=[_bump(i, off) or i for i in asm.instance_indices],
            )
            merged.assemblies.append(na)

        for at in src.attributes:
            merged.attributes.append(
                Attribute(
                    index=_bump(at.index, off) or at.index,
                    name=at.name,
                    owner_index=_bump(at.owner_index, off),
                    values=copy.deepcopy(at.values),
                )
            )

        next_offset = _max_model_id(merged) + 1000

    return merged


def parse_prt(data: bytes, *, stream_index: int | None = None) -> ParseResult:
    """Extract and parse a Parasolid XT stream from an NX ``.prt`` buffer into a :class:`ParseResult`."""
    archive = extract_prt(data)
    candidates = _select_brep_streams(archive, stream_index=stream_index)
    # Prefer the stream with the highest face count when auto-selecting a single ParseResult.
    chosen = max(candidates, key=lambda s: (s.face_count, len(s.ps_bytes))) if stream_index is None else candidates[0]
    parsed = parse_xb(chosen.raw_xb)
    parsed.header.format_kind = f"nx-prt-{archive.container}"
    return parsed


def extract_prt_brep(data: bytes, *, stream_index: int | None = None) -> BrepModel:
    """Extract and merge all Parasolid B-rep streams from an NX ``.prt`` buffer into a :class:`BrepModel`."""
    archive = extract_prt(data)
    candidates = _select_brep_streams(archive, stream_index=stream_index)
    models: list[BrepModel] = []
    for st in candidates:
        parsed = parse_xb(st.raw_xb)
        parsed.header.format_kind = f"nx-prt-{archive.container}"
        m = extract_brep(parsed)
        if m.faces:
            models.append(m)
    if not models:
        raise ValueError(f"NX .prt ({archive.container}) streams contain no B-rep faces")
    header = copy.deepcopy(models[0].header)
    if header is not None:
        header.format_kind = f"nx-prt-{archive.container}"
    return merge_brep_models(models, header=header)


def load_prt(path: str | Path, *, stream_index: int | None = None) -> ParseResult:
    """Load an NX ``.prt`` file from disk and parse its primary Parasolid XT stream."""
    return parse_prt(Path(path).read_bytes(), stream_index=stream_index)


def _split_xb_header_and_ps(xb_bytes: bytes) -> tuple[bytes, bytes]:
    ps_pos = xb_bytes.find(b"PS\x00\x00")
    if ps_pos == -1:
        raise ValueError("Input XT bytes do not contain a binary PS\\x00\\x00 stream")
    if ps_pos > 0:
        return xb_bytes[:ps_pos], xb_bytes[ps_pos:]
    return b"", xb_bytes


def build_splmsstr_prt(
    xb_streams: Sequence[bytes],
    *,
    compress: bool = True,
    preview: bytes | None = None,
) -> bytes:
    """Build a clean-room synthetic NX ``SPLMSSTR`` v6 ``.prt`` file from binary ``.x_b`` streams."""
    ug_buf = bytearray(struct.pack("<6I", 2, 1, 1, 1, 1, 32))
    ug_buf.extend(b"\x00" * 16)
    for xb in xb_streams:
        ascii_hdr, ps_bytes = _split_xb_header_and_ps(xb)
        payload = zlib.compress(ps_bytes, level=6) if compress else ps_bytes
        flags = (0x8000_0000 if compress else 0) | 0x4000_0000 | (len(ascii_hdr) & 0x3FFF_FFFF)
        ext_len = 0x25
        ug_buf.extend(_PS_RECORD_MAGIC)
        ug_buf.extend(struct.pack("<III", flags, len(payload), ext_len))
        ug_buf.extend(b"\x00" * (ext_len - 16))
        ug_buf.extend(ascii_hdr)
        ug_buf.extend(payload)

    entries: list[tuple[str, bytes | None]] = [
        ("/Root/", None),
        ("/Root/UG_PART/", None),
        ("/Root/UG_PART/UG_PART", bytes(ug_buf)),
    ]
    if preview is not None:
        entries.append(("/Root/images/", None))
        entries.append(("/Root/images/preview", preview))

    dir_size = sum(4 + len(p) + 16 for p, _ in entries)
    header_total = 35 + dir_size

    out = bytearray(SPLMSSTR_MAGIC)
    out.append(6)  # version
    out.extend(b"\x00" * 16)  # placeholder offsets patched below
    out.extend(b"HEADER")
    out.extend(struct.pack("<I", len(entries)))

    data_chunks = bytearray()
    cur_off = header_total
    ug_off = 0
    for path, blob in entries:
        p_bytes = path.encode("ascii")
        out.extend(struct.pack("<I", len(p_bytes)))
        out.extend(p_bytes)
        if blob is None:
            out.extend(b"\x00" * 16)
        else:
            if path == "/Root/UG_PART/UG_PART":
                ug_off = cur_off
            out.extend(struct.pack("<QQ", cur_off, len(blob)))
            data_chunks.extend(blob)
            cur_off += len(blob)

    footer_off = header_total + len(data_chunks)
    struct.pack_into("<QQ", out, 9, ug_off, footer_off)
    out.extend(data_chunks)
    return bytes(out)


def build_ole2_prt(xb_streams: Sequence[bytes]) -> bytes:
    """Build a clean-room minimal OLE2/MS-CFB ``.prt`` file containing ``UG_PART``."""
    ug_buf = bytearray(b"..UGII  " + b"\x00" * 64)
    for xb in xb_streams:
        ascii_hdr, ps_bytes = _split_xb_header_and_ps(xb)
        flags = len(ascii_hdr) & 0x3FFF_FFFF
        ug_buf.extend(_PS_RECORD_MAGIC)
        ug_buf.extend(struct.pack("<II", flags, len(ps_bytes)))
        ug_buf.extend(ascii_hdr)
        ug_buf.extend(ps_bytes)

    sec_size = 512
    ug_bytes = bytes(ug_buf)
    ug_secs = max(1, (len(ug_bytes) + sec_size - 1) // sec_size)
    # Layout: sector 0 = FAT, sector 1 = Directory, sectors 2..2+ug_secs-1 = UG_PART
    fat_sec = 0
    dir_sec = 1
    ug_start = 2

    fat = [0xFFFFFFFF] * 128
    fat[fat_sec] = 0xFFFFFFFD  # FATSECT
    fat[dir_sec] = 0xFFFFFFFE  # ENDOFCHAIN
    for s in range(ug_start, ug_start + ug_secs):
        fat[s] = s + 1 if s + 1 < ug_start + ug_secs else 0xFFFFFFFE

    def _dir_entry(name: str, obj_type: int, start: int, size: int, child: int = 0xFFFFFFFF) -> bytes:
        buf = bytearray(128)
        raw_name = name.encode("utf-16le") + b"\x00\x00"
        buf[: len(raw_name)] = raw_name
        struct.pack_into("<H", buf, 64, len(raw_name))
        buf[66] = obj_type
        buf[67] = 1  # black RB node
        struct.pack_into("<III", buf, 68, 0xFFFFFFFF, 0xFFFFFFFF, child)
        struct.pack_into("<II", buf, 116, start, size)
        return bytes(buf)

    dir_buf = bytearray(sec_size)
    dir_buf[0:128] = _dir_entry("Root Entry", 5, 0xFFFFFFFE, 0, child=1)
    dir_buf[128:256] = _dir_entry("UG_PART", 2, ug_start, len(ug_bytes))

    hdr = bytearray(sec_size)
    hdr[0:8] = OLE2_MAGIC
    struct.pack_into("<HHHH", hdr, 24, 0x003E, 0x0003, 0xFFFE, 9)
    struct.pack_into("<H", hdr, 32, 6)
    struct.pack_into("<IIIIII", hdr, 44, 1, dir_sec, 0, 0, 0xFFFFFFFE, 0)
    struct.pack_into("<II", hdr, 68, 0xFFFFFFFE, 0)
    difat = [0xFFFFFFFF] * 109
    difat[0] = fat_sec
    struct.pack_into("<109I", hdr, 76, *difat)

    fat_bytes = struct.pack("<128I", *fat)
    ug_padded = ug_bytes.ljust(ug_secs * sec_size, b"\x00")
    return bytes(hdr) + fat_bytes + bytes(dir_buf) + ug_padded
