"""xtopen: Clean-room Parasolid XT (.x_b, .x_t, .x_p, .x_d) reader, inspector, and STEP exporter."""

from .schema import builtin_schema, parse_schema, effective
from .xb import (
    XtHeader,
    XtNode,
    ParseResult,
    DeltaResult,
    load_xb,
    parse_xb,
    parse_delta,
)
from .xt import load_xt, parse_xt
from .prt import (
    PrtArchive,
    PrtStream,
    build_ole2_prt,
    build_splmsstr_prt,
    extract_prt,
    extract_prt_brep,
    is_prt,
    load_prt,
    load_prt_archive,
    merge_brep_models,
    parse_prt,
)
from .writer import write_xb, write_xt, xb_to_xt, xt_to_xb
from .model import (
    NurbsCurve,
    NurbsSurface,
    Vertex,
    Edge,
    Coedge,
    Loop,
    Face,
    Attribute,
    Appearance,
    AssemblyInfo,
    BrepModel,
    extract_brep,
)
from .inspect import ValidationReport, validate, summarize, to_json
from .step_writer import export_step, dumps_step

__version__ = "0.4.0"

__all__ = [
    "__version__",
    "builtin_schema",
    "parse_schema",
    "effective",
    "XtHeader",
    "XtNode",
    "ParseResult",
    "DeltaResult",
    "load_xb",
    "parse_xb",
    "parse_delta",
    "load_xt",
    "parse_xt",
    "PrtArchive",
    "PrtStream",
    "is_prt",
    "extract_prt",
    "load_prt_archive",
    "parse_prt",
    "load_prt",
    "extract_prt_brep",
    "merge_brep_models",
    "build_splmsstr_prt",
    "build_ole2_prt",
    "write_xb",
    "write_xt",
    "xb_to_xt",
    "xt_to_xb",
    "NurbsCurve",
    "NurbsSurface",
    "Vertex",
    "Edge",
    "Coedge",
    "Loop",
    "Face",
    "Attribute",
    "Appearance",
    "AssemblyInfo",
    "BrepModel",
    "extract_brep",
    "ValidationReport",
    "validate",
    "summarize",
    "to_json",
    "export_step",
    "dumps_step",
]
