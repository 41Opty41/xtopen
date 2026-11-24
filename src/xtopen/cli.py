"""Command-line interface for xtopen."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .inspect import summarize, to_json, validate
from .step_writer import export_step
from .writer import write_xb, write_xt
from .xb import ParseResult, parse_delta, parse_xb
from .xt import parse_xt


def _is_text_xt(path: Path, data: bytes) -> bool:
    low = path.suffix.lower()
    if low == ".x_t":
        return True
    if low in (".x_b", ".x_p", ".x_d"):
        return False
    if b"FORMAT=text" in data[:1024]:
        return True
    stripped = data.lstrip(b" \t\r\n")
    return stripped.startswith(b"T")


def _load_any(path: Path) -> ParseResult:
    data = path.read_bytes()
    if _is_text_xt(path, data):
        return parse_xt(data)
    return parse_xb(data)


def _cmd_info(args: argparse.Namespace) -> int:
    path = Path(args.file)
    data = path.read_bytes()
    if path.suffix.lower() == ".x_d":
        res = parse_delta(data)
        payload = {
            "header": res.header.__dict__,
            "transactions": len(res.transactions),
            "remaining_bytes": res.remaining_bytes,
        }
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(f"File: {path.name} (.x_d delta)")
            print(f"Transactions: {len(res.transactions)}")
            for txn in res.transactions:
                print(f"  txn#{txn['index']}: {len(txn['nodes'])} node(s)")
        return 0

    parsed = parse_xt(data) if _is_text_xt(path, data) else parse_xb(data)
    info = summarize(parsed)
    if args.json:
        print(json.dumps(info, indent=2, default=str))
        return 0

    hdr = info["header"] or {}
    c = info["counts"]
    print(f"File: {path.name}")
    print(f"Format: {hdr.get('format_kind')} (form2={hdr.get('form2')}, schema={hdr.get('schema_name')})")
    print(
        f"Nodes: {c['nodes']} | Faces: {c['faces']} | Edges: {c['edges']} | "
        f"Vertices: {c['vertices']} | Euler V-E+F: {info['euler']}"
    )
    if info["bbox"] is not None:
        lo, hi = info["bbox"]
        print(f"BBox (m): ({lo[0]:.6g}, {lo[1]:.6g}, {lo[2]:.6g}) .. ({hi[0]:.6g}, {hi[1]:.6g}, {hi[2]:.6g})")
    if info["surface_types"]:
        print(f"Surfaces: {info['surface_types']}")
    if info["curve_types"]:
        print(f"Curves: {info['curve_types']}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    parsed = _load_any(Path(args.file))
    rep = validate(parsed)
    if args.json:
        print(
            json.dumps(
                {
                    "valid": rep.valid,
                    "errors": rep.errors,
                    "warnings": rep.warnings,
                    "stats": rep.stats,
                },
                indent=2,
                default=str,
            )
        )
    else:
        status = "VALID" if rep.valid else "INVALID"
        print(f"{status}: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
        for e in rep.errors:
            print(f"  ERROR: {e}")
        for w in rep.warnings:
            print(f"  WARN: {w}")
    return 0 if rep.valid else 1


def _cmd_dump(args: argparse.Namespace) -> int:
    parsed = _load_any(Path(args.file))
    if args.nodes_only:
        text = json.dumps(
            {
                "header": parsed.header.__dict__,
                "nodes": [n.to_dict() for n in parsed.nodes],
                "remaining_bytes": parsed.remaining_bytes,
            },
            indent=2,
            default=str,
        )
    else:
        text = to_json(parsed, indent=2)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    in_path = Path(args.input)
    out_path = Path(args.output)
    parsed = _load_any(in_path)
    ext = out_path.suffix.lower()

    if ext in (".step", ".stp"):
        export_step(parsed, out_path, schema=args.schema, units=args.units, name=in_path.stem)
    elif ext == ".x_t":
        out_path.write_bytes(write_xt(parsed))
    elif ext in (".x_b", ".x_p"):
        out_path.write_bytes(write_xb(parsed, form2=args.form2))
    else:
        raise ValueError(f"Unsupported output extension {ext!r}; use .step, .stp, .x_b, or .x_t")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xtopen",
        description="Clean-room Parasolid XT (.x_b/.x_t/.x_p/.x_d) inspector and STEP exporter.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="Print file header, topology counts, and geometry summary.")
    p_info.add_argument("file", help="Path to .x_b, .x_t, .x_p, or .x_d file.")
    p_info.add_argument("--json", action="store_true", help="Output JSON.")
    p_info.set_defaults(func=_cmd_info)

    p_val = sub.add_parser("validate", help="Validate XT parse integrity and B-rep topology.")
    p_val.add_argument("file", help="Path to .x_b, .x_t, or .x_p file.")
    p_val.add_argument("--json", action="store_true", help="Output JSON.")
    p_val.set_defaults(func=_cmd_validate)

    p_dump = sub.add_parser("dump", help="Dump decoded XT nodes and B-rep model as JSON.")
    p_dump.add_argument("file", help="Path to .x_b, .x_t, or .x_p file.")
    p_dump.add_argument("--nodes-only", action="store_true", help="Dump raw XT node list only.")
    p_dump.add_argument("-o", "--output", help="Optional output file path.")
    p_dump.set_defaults(func=_cmd_dump)

    p_conv = sub.add_parser("convert", help="Convert between .x_b, .x_t, and .step/.stp.")
    p_conv.add_argument("input", help="Input .x_b, .x_t, or .x_p file.")
    p_conv.add_argument("output", help="Output .step, .stp, .x_b, or .x_t file.")
    p_conv.add_argument("--units", choices=("mm", "m"), default="mm", help="STEP length unit (default: mm).")
    p_conv.add_argument("--schema", choices=("AP214", "AP242"), default="AP214", help="STEP schema (default: AP214).")
    p_conv.add_argument("--form2", action="store_true", help="Write Form-2 .x_b when outputting .x_b.")
    p_conv.set_defaults(func=_cmd_convert)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
