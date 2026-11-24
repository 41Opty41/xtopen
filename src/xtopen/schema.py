"""Self-contained Parasolid XT base schema derived from the public XT Format Reference.

Provides:
- builtin_schema(): built-in schema dictionary (zero external schema file required).
- parse_schema(path): optional parser for external .s_t schema files.
- effective(node): transmitted fields of a schema node definition.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path


def _f(name: str, code: str, ptr_class: int = 0, elements: int = 0, transmitted: bool = True) -> dict:
    return {
        "name": name,
        "code": code,
        "transmitted": transmitted,
        "ptr_class": ptr_class,
        "elements": elements,
    }


def _node(type_id: int, name: str, fields: list[dict], variable: bool = False, transmitted: bool = True) -> dict:
    return {
        "type": type_id,
        "name": name,
        "transmitted": transmitted,
        "variable": variable,
        "fields": fields,
    }


def _any_curve_fields() -> list[dict]:
    return [
        _f("node_id", "d"),
        _f("attributes_groups", "p", 1019),
        _f("owner", "p", 1010),
        _f("next", "p", 1008),
        _f("previous", "p", 1008),
        _f("geometric_owner", "p", 141),
        _f("sense", "c"),
    ]


def _any_surf_fields() -> list[dict]:
    return [
        _f("node_id", "d"),
        _f("attributes_groups", "p", 1019),
        _f("owner", "p", 1007),
        _f("next", "p", 1006),
        _f("previous", "p", 1006),
        _f("geometric_owner", "p", 141),
        _f("sense", "c"),
    ]


@lru_cache(maxsize=1)
def _build_builtin_schema() -> dict[int, dict]:
    """Build the standard XT base node table from the public XT Format Reference."""
    nodes = [
        # Schema / partition delta header node (used in .x_d / .x_t)
        _node(4, "SCHEMA", [
            _f("node_id", "d"),
            _f("preceding", "d"),
            _f("first_following", "d"),
            _f("next_sibling", "d"),
            _f("lattice", "p", 0, 2),
            _f("mesh", "f", 0, 2),
            _f("polyline", "p", 0, 2),
            _f("n_new_nodes", "d"),
            _f("n_del_nodes", "d"),
            _f("n_copy_mod_nodes", "d"),
            _f("delta_key", "d"),
            _f("delta_is_forward", "l"),
            _f("id", "d"),
            _f("index_map_offset", "d"),
        ]),
        # Topology nodes (10..19)
        _node(10, "ASSEMBLY", [
            _f("highest_node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("attribute_chains", "p", 70),
            _f("list", "p", 70),
            _f("surface", "p", 1006),
            _f("curve", "p", 1008),
            _f("point", "p", 29),
            _f("key", "p", 102),
            _f("res_size", "f"),
            _f("res_linear", "f"),
            _f("ref_instance", "p", 11),
            _f("next", "p", 10),
            _f("previous", "p", 10),
            _f("state", "u"),
            _f("owner", "p", 101),
            _f("type", "u"),
            _f("sub_instance", "p", 11),
        ]),
        _node(11, "INSTANCE", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("type", "u"),
            _f("part", "p", 1005),
            _f("transform", "p", 100),
            _f("assembly", "p", 10),
            _f("next_in_part", "p", 11),
            _f("prev_in_part", "p", 11),
            _f("next_of_part", "p", 11),
            _f("prev_of_part", "p", 11),
        ]),
        _node(12, "BODY", [
            _f("highest_node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("attribute_chains", "p", 70),
            _f("surface", "p", 1006),
            _f("curve", "p", 1008),
            _f("point", "p", 29),
            _f("key", "p", 102),
            _f("res_size", "f"),
            _f("res_linear", "f"),
            _f("ref_instance", "p", 11),
            _f("next", "p", 12),
            _f("previous", "p", 12),
            _f("state", "u"),
            _f("owner", "p", 101),
            _f("body_type", "u"),
            _f("nom_geom_state", "u"),
            _f("shell", "p", 13),
            _f("boundary_surface", "p", 1006),
            _f("boundary_curve", "p", 1008),
            _f("boundary_point", "p", 29),
            _f("region", "p", 19),
            _f("edge", "p", 16),
            _f("vertex", "p", 18),
        ]),
        _node(13, "SHELL", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("body", "p", 12),
            _f("next", "p", 13),
            _f("face", "p", 14),
            _f("edge", "p", 16),
            _f("vertex", "p", 18),
            _f("region", "p", 19),
            _f("front_face", "p", 14),
        ]),
        _node(14, "FACE", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("tolerance", "f"),
            _f("next", "p", 14),
            _f("previous", "p", 14),
            _f("loop", "p", 15),
            _f("shell", "p", 13),
            _f("surface", "p", 1006),
            _f("sense", "c"),
            _f("next_on_surface", "p", 14),
            _f("previous_on_surface", "p", 14),
            _f("next_front", "p", 14),
            _f("previous_front", "p", 14),
            _f("front_shell", "p", 13),
        ]),
        _node(15, "LOOP", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("halfedge", "p", 17),
            _f("face", "p", 14),
            _f("next", "p", 15),
        ]),
        _node(16, "EDGE", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("tolerance", "f"),
            _f("halfedge", "p", 17),
            _f("previous", "p", 16),
            _f("next", "p", 16),
            _f("curve", "p", 1008),
            _f("next_on_curve", "p", 16),
            _f("previous_on_curve", "p", 16),
            _f("owner", "p", 1029),
        ]),
        _node(17, "HALFEDGE", [
            _f("attributes_groups", "p", 1019),
            _f("loop", "p", 15),
            _f("forward", "p", 17),
            _f("backward", "p", 17),
            _f("vertex", "p", 18),
            _f("other", "p", 17),
            _f("edge", "p", 16),
            _f("curve", "p", 1008),
            _f("next_at_vx", "p", 17),
            _f("sense", "c"),
        ]),
        _node(18, "VERTEX", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("halfedge", "p", 17),
            _f("previous", "p", 18),
            _f("next", "p", 18),
            _f("point", "p", 29),
            _f("tolerance", "f"),
            _f("owner", "p", 1029),
        ]),
        _node(19, "REGION", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("body", "p", 12),
            _f("next", "p", 19),
            _f("previous", "p", 19),
            _f("shell", "p", 13),
            _f("type", "c"),
        ]),
        # Point & Curves
        _node(29, "POINT", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("owner", "p", 1011),
            _f("next", "p", 29),
            _f("previous", "p", 29),
            _f("pvec", "v"),
        ]),
        _node(30, "LINE", _any_curve_fields() + [
            _f("pvec", "v"),
            _f("direction", "v"),
        ]),
        _node(31, "CIRCLE", _any_curve_fields() + [
            _f("centre", "v"),
            _f("normal", "v"),
            _f("x_axis", "v"),
            _f("radius", "f"),
        ]),
        _node(32, "ELLIPSE", _any_curve_fields() + [
            _f("centre", "v"),
            _f("normal", "v"),
            _f("x_axis", "v"),
            _f("major_radius", "f"),
            _f("minor_radius", "f"),
        ]),
        _node(38, "INTERSECTION", _any_curve_fields() + [
            _f("surface", "p", 1006, 2),
            _f("chart", "p", 40),
            _f("start", "p", 41),
            _f("end", "p", 41),
        ]),
        _node(40, "CHART", [
            _f("base_parameter", "f"),
            _f("base_scale", "f"),
            _f("chart_count", "d"),
            _f("chordal_error", "f"),
            _f("angular_error", "f"),
            _f("parameter_error", "f", 0, 2),
            _f("hvec", "h", 0, 1),
        ], variable=True),
        _node(41, "LIMIT", [
            _f("type", "c"),
            _f("hvec", "h", 0, 1),
        ], variable=True),
        _node(45, "BSPLINE_VERTICES", [
            _f("vertices", "f", 0, 1),
        ], variable=True),
        # Surfaces
        _node(50, "PLANE", _any_surf_fields() + [
            _f("pvec", "v"),
            _f("normal", "v"),
            _f("x_axis", "v"),
        ]),
        _node(51, "CYLINDER", _any_surf_fields() + [
            _f("pvec", "v"),
            _f("axis", "v"),
            _f("radius", "f"),
            _f("x_axis", "v"),
        ]),
        _node(52, "CONE", _any_surf_fields() + [
            _f("pvec", "v"),
            _f("axis", "v"),
            _f("radius", "f"),
            _f("sin_half_angle", "f"),
            _f("cos_half_angle", "f"),
            _f("x_axis", "v"),
        ]),
        _node(53, "SPHERE", _any_surf_fields() + [
            _f("centre", "v"),
            _f("radius", "f"),
            _f("axis", "v"),
            _f("x_axis", "v"),
        ]),
        _node(54, "TORUS", _any_surf_fields() + [
            _f("centre", "v"),
            _f("axis", "v"),
            _f("major_radius", "f"),
            _f("minor_radius", "f"),
            _f("x_axis", "v"),
        ]),
        _node(56, "BLENDED_EDGE", _any_surf_fields() + [
            _f("blend_type", "c"),
            _f("surface", "p", 1006, 2),
            _f("spine", "p", 1008),
            _f("range", "f", 0, 2),
            _f("thumb_weight", "f", 0, 2),
            _f("boundary", "p", 1006, 2),
            _f("start", "p", 41),
            _f("end", "p", 41),
        ]),
        _node(59, "BLEND_BOUND", _any_surf_fields() + [
            _f("boundary", "n"),
            _f("blend", "p", 1006),
        ]),
        _node(60, "OFFSET_SURF", _any_surf_fields() + [
            _f("check", "c"),
            _f("true_offset", "l"),
            _f("surface", "p", 1006),
            _f("offset", "f"),
            _f("scale", "f"),
        ]),
        _node(67, "SWEPT_SURF", _any_surf_fields() + [
            _f("section", "p", 1008),
            _f("sweep", "v"),
            _f("scale", "f"),
        ]),
        _node(68, "SPUN_SURF", _any_surf_fields() + [
            _f("profile", "p", 1008),
            _f("base", "v"),
            _f("axis", "v"),
            _f("start", "v"),
            _f("end", "v"),
            _f("start_param", "f"),
            _f("end_param", "f"),
            _f("x_axis", "v"),
            _f("scale", "f"),
        ]),
        # Associated data (lists, attributes, groups, world, transform, key)
        _node(70, "LIST", [
            _f("node_id", "d"),
            _f("owner", "p", 1013),
            _f("next", "p", 70),
            _f("previous", "p", 70),
            _f("list_type", "d"),
            _f("list_length", "d"),
            _f("block_length", "d"),
            _f("size_of_entry", "d"),
            _f("list_block", "p", 1012),
            _f("finger_block", "p", 1012),
            _f("finger_index", "d"),
            _f("notransmit", "l"),
        ]),
        _node(74, "POINTER_LIS_BLOCK", [
            _f("n_entries", "d"),
            _f("next_block", "p", 74),
            _f("entries", "p", 0, 1),
        ], variable=True),
        _node(79, "ATT_DEF_ID", [
            _f("string", "c", 0, 1),
        ], variable=True),
        _node(80, "ATTRIB_DEF", [
            _f("next", "p", 80),
            _f("identifier", "p", 79),
            _f("type_id", "d"),
            _f("actions", "u", 0, 8),
            _f("field_names", "p", 99),
            _f("legal_owners", "l", 0, 14),
            _f("fields", "u", 0, 1),
        ], variable=True),
        _node(81, "ATTRIBUTE", [
            _f("node_id", "d"),
            _f("definition", "p", 80),
            _f("owner", "p", 1015),
            _f("next", "p", 1019),
            _f("previous", "p", 1019),
            _f("next_of_type", "p", 81),
            _f("previous_of_type", "p", 81),
            _f("fields", "p", 1018, 1),
        ], variable=True),
        _node(82, "INT_VALUES", [_f("values", "d", 0, 1)], variable=True),
        _node(83, "REAL_VALUES", [_f("values", "f", 0, 1)], variable=True),
        _node(84, "CHAR_VALUES", [_f("values", "c", 0, 1)], variable=True),
        _node(85, "POINT_VALUES", [_f("values", "v", 0, 1)], variable=True),
        _node(86, "VECTOR_VALUES", [_f("values", "v", 0, 1)], variable=True),
        _node(87, "AXIS_VALUES", [_f("values", "v", 0, 1)], variable=True),
        _node(88, "TAG_VALUES", [_f("values", "t", 0, 1)], variable=True),
        _node(89, "DIRECTION_VALUES", [_f("values", "v", 0, 1)], variable=True),
        _node(90, "GROUP", [
            _f("node_id", "d"),
            _f("attributes_groups", "p", 1019),
            _f("owner", "p", 1005),
            _f("next", "p", 1019),
            _f("previous", "p", 1019),
            _f("type", "u"),
            _f("first_member", "p", 91),
        ]),
        _node(91, "MEMBER_OF_GROUP", [
            _f("dummy_node_id", "d"),
            _f("owning_group", "p", 90),
            _f("owner", "p", 1017),
            _f("next", "p", 1019),
            _f("previous", "p", 1019),
            _f("next_member", "p", 91),
            _f("previous_member", "p", 91),
        ]),
        _node(98, "UNICODE_VALUES", [_f("values", "w", 0, 1)], variable=True),
        _node(99, "FIELD_NAMES", [_f("names", "p", 1037, 1)], variable=True),
        _node(100, "TRANSFORM", [
            _f("node_id", "d"),
            _f("owner", "p", 1023),
            _f("next", "p", 100),
            _f("previous", "p", 100),
            _f("rotation_matrix", "f", 0, 9),
            _f("translation_vector", "v"),
            _f("scale", "f"),
            _f("flag", "d"),
            _f("perspective_vector", "v"),
        ]),
        _node(101, "WORLD", [
            _f("assembly", "p", 10),
            _f("attribute", "p", 81),
            _f("body", "p", 12),
            _f("transform", "p", 100),
            _f("surface", "p", 1006),
            _f("curve", "p", 1008),
            _f("point", "p", 29),
            _f("alive", "l"),
            _f("attrib_def", "p", 80),
            _f("highest_id", "d"),
            _f("current_id", "d"),
        ]),
        _node(102, "KEY", [_f("string", "c", 0, 1)], variable=True),
        # Foreign / NURBS surfaces & curves
        _node(120, "PE_SURF", _any_surf_fields() + [
            _f("type", "c"),
            _f("data", "p", 1027),
            _f("tf", "p", 100),
            _f("internal_geom", "p", 1028, 1),
        ], variable=True),
        _node(121, "INT_PE_DATA", [
            _f("geom_type", "d"),
            _f("real_array", "p", 83),
            _f("int_array", "p", 82),
        ]),
        _node(122, "EXT_PE_DATA", [
            _f("key", "p", 102),
            _f("real_array", "p", 83),
            _f("int_array", "p", 82),
            _f("data", "f", 0, 1),
        ], variable=True),
        _node(124, "B_SURFACE", _any_surf_fields() + [
            _f("nurbs", "p", 126),
            _f("data", "p", 125),
        ]),
        _node(125, "SURFACE_DATA", [
            _f("original_uint", "i"),
            _f("original_vint", "i"),
            _f("extended_uint", "i"),
            _f("extended_vint", "i"),
            _f("self_int", "u"),
            _f("original_u_start", "c"),
            _f("original_u_end", "c"),
            _f("original_v_start", "c"),
            _f("original_v_end", "c"),
            _f("extended_u_start", "c"),
            _f("extended_u_end", "c"),
            _f("extended_v_start", "c"),
            _f("extended_v_end", "c"),
            _f("analytic_form_type", "c"),
            _f("swept_form_type", "c"),
            _f("spun_form_type", "c"),
            _f("blend_form_type", "c"),
            _f("analytic_form", "p", 0),
            _f("swept_form", "p", 0),
            _f("spun_form", "p", 0),
            _f("blend_form", "p", 0),
        ]),
        _node(126, "NURBS_SURF", [
            _f("u_periodic", "l"),
            _f("v_periodic", "l"),
            _f("u_degree", "n"),
            _f("v_degree", "n"),
            _f("n_u_vertices", "d"),
            _f("n_v_vertices", "d"),
            _f("u_knot_type", "u"),
            _f("v_knot_type", "u"),
            _f("n_u_knots", "d"),
            _f("n_v_knots", "d"),
            _f("rational", "l"),
            _f("u_closed", "l"),
            _f("v_closed", "l"),
            _f("surface_form", "u"),
            _f("vertex_dim", "n"),
            _f("bspline_vertices", "p", 45),
            _f("u_knot_mult", "p", 127),
            _f("v_knot_mult", "p", 127),
            _f("u_knots", "p", 128),
            _f("v_knots", "p", 128),
        ]),
        _node(127, "KNOT_MULT", [_f("mult", "n", 0, 1)], variable=True),
        _node(128, "KNOT_SET", [_f("knots", "f", 0, 1)], variable=True),
        _node(130, "PE_CURVE", _any_curve_fields() + [
            _f("type", "c"),
            _f("data", "p", 1027),
            _f("tf", "p", 100),
            _f("internal_geom", "p", 1028, 1),
        ], variable=True),
        _node(133, "TRIMMED_CURVE", _any_curve_fields() + [
            _f("basis_curve", "p", 1008),
            _f("point_1", "v"),
            _f("point_2", "v"),
            _f("parm_1", "f"),
            _f("parm_2", "f"),
        ]),
        _node(134, "B_CURVE", _any_curve_fields() + [
            _f("nurbs", "p", 136),
            _f("data", "p", 135),
        ]),
        _node(135, "CURVE_DATA", [
            _f("self_int", "u"),
            _f("analytic_form", "p", 0),
        ]),
        _node(136, "NURBS_CURVE", [
            _f("degree", "n"),
            _f("n_vertices", "d"),
            _f("vertex_dim", "n"),
            _f("n_knots", "d"),
            _f("knot_type", "u"),
            _f("periodic", "l"),
            _f("closed", "l"),
            _f("rational", "l"),
            _f("curve_form", "u"),
            _f("bspline_vertices", "p", 45),
            _f("knot_mult", "p", 127),
            _f("knots", "p", 128),
        ]),
        _node(137, "SP_CURVE", _any_curve_fields() + [
            _f("surface", "p", 1006),
            _f("b_curve", "p", 134),
            _f("original", "p", 1008),
            _f("tolerance_to_original", "f"),
        ]),
        _node(141, "GEOMETRIC_OWNER", [
            _f("owner", "p", 1003),
            _f("next", "p", 141),
            _f("previous", "p", 141),
            _f("shared_geometry", "p", 1003),
        ]),
        _node(163, "HELIX_CU_FORM", [
            _f("axis_pt", "v"),
            _f("axis_dir", "v"),
            _f("hand", "c"),
            _f("turns", "i"),
            _f("pitch", "f"),
            _f("gap", "f"),
            _f("tol", "f"),
        ]),
        _node(184, "HELIX_SU_FORM", [
            _f("axis_pt", "v"),
            _f("axis_dir", "v"),
            _f("point", "v"),
            _f("hand", "c"),
            _f("turns", "i"),
            _f("pitch", "f"),
            _f("tol", "f"),
        ]),
    ]
    return {n["type"]: n for n in nodes}


def builtin_schema() -> dict[int, dict]:
    """Return a fresh copy of the built-in Parasolid XT base schema."""
    return deepcopy(_build_builtin_schema())


def parse_schema(path: str | Path) -> dict[int, dict]:
    """Parse an external Parasolid .s_t schema file into a node dictionary."""
    nodes: dict[int, dict] = {}
    cur: int | None = None
    lines = Path(path).read_bytes().split(b"\n")
    started = False
    for raw in lines:
        line = raw.decode("ascii", "replace").rstrip("\r")
        if line.startswith("**END_OF_HEADER"):
            started = True
            continue
        if not started:
            continue
        s = line.strip()
        if not s:
            continue
        parts = s.split(";")
        if len(parts) == 3 and parts[0].strip() and parts[0].strip()[0].isdigit():
            num, name = parts[0].strip().split(" ", 1)
            tail = parts[2].split()
            cur = int(num)
            nodes[cur] = {
                "type": cur,
                "name": name.strip(),
                "transmitted": tail[0] == "1",
                "variable": tail[2] == "1",
                "fields": [],
            }
        elif len(parts) == 3 and cur is not None:
            fname = parts[0].strip()
            code = parts[1].strip()
            tail = parts[2].split()
            nodes[cur]["fields"].append(
                {
                    "name": fname,
                    "code": code,
                    "transmitted": tail[0] == "1",
                    "ptr_class": int(tail[1]),
                    "elements": int(tail[2]),
                }
            )
    return nodes


def effective(node: dict) -> list[dict]:
    """Return transmitted fields (plus variable-length trailing fields) in schema order."""
    return [f for f in node["fields"] if f["transmitted"] or f["elements"] == 1]
