"""Native zero-dependency ISO-10303-21 STEP AP214/AP242 writer for XT B-rep models."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any

from .model import (
    Appearance,
    AssemblyInfo,
    BrepModel,
    Coedge,
    Edge,
    Face,
    Loop,
    NurbsCurve,
    NurbsSurface,
    extract_brep,
)
from .xb import ParseResult


def _v_norm(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-15:
        return (0.0, 0.0, 1.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _v_dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _v_cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _ortho_frame(
    z_raw: tuple[float, float, float],
    x_raw: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    z = _v_norm(z_raw)
    dot = _v_dot(x_raw, z)
    xp = (x_raw[0] - dot * z[0], x_raw[1] - dot * z[1], x_raw[2] - dot * z[2])
    if math.sqrt(_v_dot(xp, xp)) < 1e-12:
        cand = (1.0, 0.0, 0.0) if abs(z[0]) < 0.9 else (0.0, 1.0, 0.0)
        xp = _v_cross(z, cand)
    return z, _v_norm(xp)


def _step_real(val: float) -> str:
    if not math.isfinite(val) or abs(val) < 1e-15:
        return "0."
    s = f"{float(val):.12g}".replace("e", "E")
    if "." not in s:
        if "E" in s:
            s = s.replace("E", ".E")
        else:
            s += "."
    return s


def _step_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _step_bool(flag: bool) -> str:
    return ".T." if flag else ".F."


def _expand_knots(knots: list[float], mults: list[int]) -> list[float]:
    out: list[float] = []
    for k, m in zip(knots, mults):
        out.extend([float(k)] * int(m))
    return out


def _eval_bspline(
    t: float,
    degree: int,
    knots: list[float],
    mults: list[int],
    poles: list[tuple[float, ...]],
    weights: list[float] | None = None,
) -> tuple[float, ...]:
    U = _expand_knots(knots, mults)
    n = len(poles) - 1
    p = min(degree, n)
    if t <= U[p]:
        k = p
        while k < n and U[k + 1] == U[p]:
            k += 1
    elif t >= U[n + 1]:
        k = n
    else:
        lo, hi = p, n + 1
        mid = (lo + hi) // 2
        while t < U[mid] or t >= U[mid + 1]:
            if t < U[mid]:
                hi = mid
            else:
                lo = mid
            mid = (lo + hi) // 2
            if hi - lo <= 1:
                break
        k = mid

    dim = len(poles[0])
    d = []
    for j in range(p + 1):
        idx = max(0, min(n, k - p + j))
        w = weights[idx] if weights is not None else 1.0
        pt = poles[idx]
        d.append([pt[c] * w for c in range(dim)] + ([w] if weights is not None else []))

    for r in range(1, p + 1):
        for j in range(p, r - 1, -1):
            i = k - p + j
            denom = U[i + p + 1 - r] - U[i]
            alpha = 0.0 if abs(denom) < 1e-15 else (t - U[i]) / denom
            d[j] = [(1.0 - alpha) * d[j - 1][c] + alpha * d[j][c] for c in range(len(d[0]))]

    res = d[p]
    if weights is not None:
        w = res[-1] if abs(res[-1]) > 1e-15 else 1.0
        return tuple(res[c] / w for c in range(dim))
    return tuple(res[:dim])


def _eval_surface_pt(surf: dict[str, Any], u: float, v: float) -> tuple[float, float, float]:
    st = surf.get("type", "plane")
    if st == "plane":
        o = surf["origin"]
        z, x = _ortho_frame(surf["normal"], surf["x_axis"])
        y = _v_cross(z, x)
        return (
            o[0] + u * x[0] + v * y[0],
            o[1] + u * x[1] + v * y[1],
            o[2] + u * x[2] + v * y[2],
        )
    if st == "cylinder":
        o = surf["origin"]
        r = float(surf["radius"])
        z, x = _ortho_frame(surf["axis"], surf["x_axis"])
        y = _v_cross(z, x)
        cu, su = math.cos(u), math.sin(u)
        return (
            o[0] + r * (cu * x[0] + su * y[0]) + v * z[0],
            o[1] + r * (cu * x[1] + su * y[1]) + v * z[1],
            o[2] + r * (cu * x[2] + su * y[2]) + v * z[2],
        )
    if st == "cone":
        # Parasolid CONE: R(u, v) = P - v*A + (X*cos u + Y*sin u)*(r + v*tan(alpha))
        o = surf["origin"]
        r0 = float(surf["radius"])
        sa = float(surf["sin_half_angle"])
        ca = float(surf["cos_half_angle"])
        tan_a = sa / ca if abs(ca) > 1e-15 else 0.0
        z, x = _ortho_frame(surf["axis"], surf["x_axis"])
        y = _v_cross(z, x)
        rad = r0 + v * tan_a
        cu, su = math.cos(u), math.sin(u)
        return (
            o[0] - v * z[0] + rad * (cu * x[0] + su * y[0]),
            o[1] - v * z[1] + rad * (cu * x[1] + su * y[1]),
            o[2] - v * z[2] + rad * (cu * x[2] + su * y[2]),
        )
    if st == "sphere":
        o = surf["centre"]
        r = float(surf["radius"])
        z, x = _ortho_frame(surf["axis"], surf["x_axis"])
        y = _v_cross(z, x)
        cu, su = math.cos(u), math.sin(u)
        cv, sv = math.cos(v), math.sin(v)
        return (
            o[0] + r * (cv * (cu * x[0] + su * y[0]) + sv * z[0]),
            o[1] + r * (cv * (cu * x[1] + su * y[1]) + sv * z[1]),
            o[2] + r * (cv * (cu * x[2] + su * y[2]) + sv * z[2]),
        )
    if st == "torus":
        o = surf["centre"]
        rmaj = float(surf["major_radius"])
        rmin = float(surf["minor_radius"])
        z, x = _ortho_frame(surf["axis"], surf["x_axis"])
        y = _v_cross(z, x)
        cu, su = math.cos(u), math.sin(u)
        cv, sv = math.cos(v), math.sin(v)
        radial = rmaj + rmin * cv
        return (
            o[0] + radial * (cu * x[0] + su * y[0]) + rmin * sv * z[0],
            o[1] + radial * (cu * x[1] + su * y[1]) + rmin * sv * z[1],
            o[2] + radial * (cu * x[2] + su * y[2]) + rmin * sv * z[2],
        )
    if st == "nurbs":
        ns: NurbsSurface | None = surf.get("nurbs")
        if ns is not None and ns.poles:
            row_pts = [
                _eval_bspline(
                    v,
                    ns.v_degree,
                    ns.v_knots,
                    ns.v_mults,
                    row,
                    ns.weights[i] if ns.weights else None,
                )
                for i, row in enumerate(ns.poles)
            ]
            pt = _eval_bspline(u, ns.u_degree, ns.u_knots, ns.u_mults, row_pts, None)
            return (pt[0], pt[1], pt[2])
    return (u, v, 0.0)


def _interpolate_cubic_bspline(pts: list[tuple[float, float, float]]) -> NurbsCurve:
    """Create a degree-1 or degree-3 clamped 3D B-spline curve through sampled points."""
    n = len(pts)
    if n <= 2:
        return NurbsCurve(
            degree=1,
            poles=list(pts),
            knots=[0.0, 1.0],
            multiplicities=[2, 2],
        )

    # Chord-length parameterization
    params = [0.0]
    for i in range(1, n):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        dz = pts[i][2] - pts[i - 1][2]
        params.append(params[-1] + math.sqrt(dx * dx + dy * dy + dz * dz))
    total = params[-1]
    if total < 1e-15:
        return NurbsCurve(degree=1, poles=[pts[0], pts[-1]], knots=[0.0, 1.0], multiplicities=[2, 2])
    params = [p / total for p in params]

    # Degree-1 polyline fallback if tiny point count, else cubic B-spline approximation via endpoints + interior fit
    if n == 3:
        return NurbsCurve(
            degree=2,
            poles=list(pts),
            knots=[0.0, 1.0],
            multiplicities=[3, 3],
        )

    # Clamped cubic B-spline with parameters as internal knots and least-squares / direct interpolation
    # For N interpolation points with cubic B-spline and clamped ends (C2): N+2 control points, N internal knots
    # Using uniform-clamped / chord knots with exact interior knot placement -> solves (N-2)x(N-2) system
    deg = 3
    m = n - 1
    knots_u = [0.0] * 4 + params[1:-1] + [1.0] * 4
    n_poles = n + 2

    def basis(i: int, p: int, u: float) -> float:
        N = [0.0] * (p + 1)
        if (i == 0 and u == knots_u[0]) or (i == n_poles - 1 and u == knots_u[-1]):
            return 1.0 if (i == 0 and u == 0.0) or (i == n_poles - 1 and u == 1.0) else 0.0
        for j in range(p + 1):
            if knots_u[i + j] <= u < knots_u[i + j + 1]:
                N[j] = 1.0
        for k in range(1, p + 1):
            saved = 0.0 if N[0] == 0.0 else ((u - knots_u[i]) * N[0]) / max(1e-15, knots_u[i + k] - knots_u[i])
            for j in range(p - k + 1):
                u_left = knots_u[i + j + 1]
                u_right = knots_u[i + j + k + 1]
                if N[j + 1] == 0.0:
                    N[j] = saved
                    saved = 0.0
                else:
                    temp = N[j + 1] / max(1e-15, u_right - u_left)
                    N[j] = saved + (u_right - u) * temp
                    saved = (u - u_left) * temp
        return N[0]

    # Natural end condition: P_1 = (2*Q_0 + Q_1)/3, P_n = (Q_{m-1} + 2*Q_m)/3 -> solves interior P_2..P_{n-1}
    # Even simpler and unconditionally stable: degree-1 / clamped Greville-averaged spline?
    # Use Gaussian elimination on full n_poles x n_poles collocation with natural end conditions:
    A = [[0.0] * n_poles for _ in range(n_poles)]
    rhs = [(0.0, 0.0, 0.0)] * n_poles
    A[0][0] = 1.0
    rhs[0] = pts[0]
    A[n_poles - 1][n_poles - 1] = 1.0
    rhs[n_poles - 1] = pts[-1]
    # Natural second-derivative zero at t=0 and t=1 -> linear relation on P0, P1, P2 and P_{n-1}, P_n, P_{n+1}
    A[1][0], A[1][1], A[1][2] = 1.0, -2.0, 1.0
    rhs[1] = (0.0, 0.0, 0.0)
    A[n_poles - 2][n_poles - 3], A[n_poles - 2][n_poles - 2], A[n_poles - 2][n_poles - 1] = 1.0, -2.0, 1.0
    rhs[n_poles - 2] = (0.0, 0.0, 0.0)
    for row_idx in range(1, m):
        u = params[row_idx]
        r = row_idx + 1
        for col in range(n_poles):
            A[r][col] = basis(col, deg, u)
        rhs[r] = pts[row_idx]

    # Solve 3 RHS coordinates with partial pivoting
    solved = [list(c) for c in rhs]
    for col in range(n_poles):
        piv = max(range(col, n_poles), key=lambda r: abs(A[r][col]))
        A[col], A[piv] = A[piv], A[col]
        solved[col], solved[piv] = solved[piv], solved[col]
        diag = A[col][col]
        if abs(diag) < 1e-14:
            # Fallback to degree-1 exact polyline if singular
            uniq_k = [0.0] + [i / m for i in range(1, m)] + [1.0]
            mults = [2] + [1] * (m - 1) + [2]
            return NurbsCurve(degree=1, poles=list(pts), knots=uniq_k, multiplicities=mults)
        for j in range(col, n_poles):
            A[col][j] /= diag
        for c in range(3):
            solved[col][c] /= diag
        for r in range(n_poles):
            if r != col and abs(A[r][col]) > 1e-15:
                fac = A[r][col]
                for j in range(col, n_poles):
                    A[r][j] -= fac * A[col][j]
                for c in range(3):
                    solved[r][c] -= fac * solved[col][c]

    poles = [(p[0], p[1], p[2]) for p in solved]
    uniq_knots = [0.0] + params[1:-1] + [1.0]
    mults = [4] + [1] * (len(params) - 2) + [4]
    return NurbsCurve(degree=3, poles=poles, knots=uniq_knots, multiplicities=mults)


def _tf_is_identity(tf: dict[str, Any] | None) -> bool:
    if not tf:
        return True
    rot = tf.get("rotation_matrix") or [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    t = tf.get("translation") or (0.0, 0.0, 0.0)
    sc = float(tf.get("scale", 1.0))
    if len(rot) < 9:
        rot = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    id9 = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    return (
        all(abs(float(rot[i]) - id9[i]) < 1e-12 for i in range(9))
        and all(abs(float(t[i])) < 1e-12 for i in range(3))
        and abs(sc - 1.0) < 1e-12
    )


def _tf_det(tf: dict[str, Any]) -> float:
    r = tf.get("rotation_matrix") or [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    if len(r) < 9:
        return 1.0
    return (
        r[0] * (r[4] * r[8] - r[5] * r[7])
        - r[1] * (r[3] * r[8] - r[5] * r[6])
        + r[2] * (r[3] * r[7] - r[4] * r[6])
    )


def _tf_is_rigid(tf: dict[str, Any] | None) -> bool:
    if not tf:
        return True
    sc = float(tf.get("scale", 1.0))
    return abs(sc - 1.0) < 1e-9 and _tf_det(tf) > 0.0


def _tf_apply_pt(tf: dict[str, Any], p: tuple[float, float, float]) -> tuple[float, float, float]:
    r = tf.get("rotation_matrix") or [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    if len(r) < 9:
        r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    t = tf.get("translation") or (0.0, 0.0, 0.0)
    sc = float(tf.get("scale", 1.0))
    return (
        sc * (r[0] * p[0] + r[1] * p[1] + r[2] * p[2]) + t[0],
        sc * (r[3] * p[0] + r[4] * p[1] + r[5] * p[2]) + t[1],
        sc * (r[6] * p[0] + r[7] * p[1] + r[8] * p[2]) + t[2],
    )


def _tf_apply_dir(tf: dict[str, Any], d: tuple[float, float, float]) -> tuple[float, float, float]:
    r = tf.get("rotation_matrix") or [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    if len(r) < 9:
        return d
    return (
        r[0] * d[0] + r[1] * d[1] + r[2] * d[2],
        r[3] * d[0] + r[4] * d[1] + r[5] * d[2],
        r[6] * d[0] + r[7] * d[1] + r[8] * d[2],
    )


def _transform_surface_dict(surf: dict[str, Any], tf: dict[str, Any]) -> dict[str, Any]:
    out = dict(surf)
    sc = abs(float(tf.get("scale", 1.0)))
    st = surf.get("type", "unknown")
    if st == "plane":
        out["origin"] = _tf_apply_pt(tf, surf["origin"])
        out["normal"] = _tf_apply_dir(tf, surf["normal"])
        out["x_axis"] = _tf_apply_dir(tf, surf["x_axis"])
    elif st in ("cylinder", "cone"):
        out["origin"] = _tf_apply_pt(tf, surf["origin"])
        out["axis"] = _tf_apply_dir(tf, surf["axis"])
        out["x_axis"] = _tf_apply_dir(tf, surf["x_axis"])
        out["radius"] = float(surf["radius"]) * sc
    elif st == "sphere":
        out["centre"] = _tf_apply_pt(tf, surf["centre"])
        out["axis"] = _tf_apply_dir(tf, surf["axis"])
        out["x_axis"] = _tf_apply_dir(tf, surf["x_axis"])
        out["radius"] = float(surf["radius"]) * sc
    elif st == "torus":
        out["centre"] = _tf_apply_pt(tf, surf["centre"])
        out["axis"] = _tf_apply_dir(tf, surf["axis"])
        out["x_axis"] = _tf_apply_dir(tf, surf["x_axis"])
        out["major_radius"] = float(surf["major_radius"]) * sc
        out["minor_radius"] = float(surf["minor_radius"]) * sc
    elif st == "nurbs" and isinstance(surf.get("nurbs"), NurbsSurface):
        ns = surf["nurbs"]
        out["nurbs"] = NurbsSurface(
            u_degree=ns.u_degree,
            v_degree=ns.v_degree,
            u_knots=ns.u_knots,
            u_mults=ns.u_mults,
            v_knots=ns.v_knots,
            v_mults=ns.v_mults,
            poles=[[_tf_apply_pt(tf, p) for p in row] for row in ns.poles],
            weights=ns.weights,
            u_periodic=ns.u_periodic,
            v_periodic=ns.v_periodic,
            u_closed=ns.u_closed,
            v_closed=ns.v_closed,
            rational=ns.rational,
        )
    elif st == "swept":
        out["direction"] = _tf_apply_dir(tf, surf["direction"])
        if isinstance(surf.get("profile"), dict):
            out["profile"] = _transform_curve_dict(surf["profile"], tf)
    elif st == "spun":
        out["base"] = _tf_apply_pt(tf, surf["base"])
        out["axis"] = _tf_apply_dir(tf, surf["axis"])
        out["x_axis"] = _tf_apply_dir(tf, surf["x_axis"])
        if isinstance(surf.get("profile"), dict):
            out["profile"] = _transform_curve_dict(surf["profile"], tf)
    elif st == "offset":
        out["offset"] = float(surf["offset"]) * sc
        if isinstance(surf.get("base_surface"), dict):
            out["base_surface"] = _transform_surface_dict(surf["base_surface"], tf)
    return out


def _transform_curve_dict(curve: dict[str, Any], tf: dict[str, Any]) -> dict[str, Any]:
    out = dict(curve)
    sc = abs(float(tf.get("scale", 1.0)))
    flip = _tf_det(tf) < 0.0
    ct = curve.get("type", "unknown")
    if ct == "line":
        out["origin"] = _tf_apply_pt(tf, curve["origin"])
        out["dir"] = _tf_apply_dir(tf, curve["dir"])
    elif ct == "circle":
        n = _tf_apply_dir(tf, curve["normal"])
        out["centre"] = _tf_apply_pt(tf, curve["centre"])
        out["normal"] = (-n[0], -n[1], -n[2]) if flip else n
        out["x_axis"] = _tf_apply_dir(tf, curve["x_axis"])
        out["radius"] = float(curve["radius"]) * sc
    elif ct == "ellipse":
        n = _tf_apply_dir(tf, curve["normal"])
        out["centre"] = _tf_apply_pt(tf, curve["centre"])
        out["normal"] = (-n[0], -n[1], -n[2]) if flip else n
        out["x_axis"] = _tf_apply_dir(tf, curve["x_axis"])
        out["major_radius"] = float(curve["major_radius"]) * sc
        out["minor_radius"] = float(curve["minor_radius"]) * sc
    elif ct == "nurbs" and isinstance(curve.get("nurbs"), NurbsCurve):
        nc = curve["nurbs"]
        if not nc.is_pcurve:
            out["nurbs"] = NurbsCurve(
                degree=nc.degree,
                poles=[_tf_apply_pt(tf, p) for p in nc.poles],
                knots=nc.knots,
                multiplicities=nc.multiplicities,
                weights=nc.weights,
                periodic=nc.periodic,
                closed=nc.closed,
                rational=nc.rational,
                vertex_dim=nc.vertex_dim,
                is_pcurve=nc.is_pcurve,
                poles_2d=nc.poles_2d,
                host_surface_index=nc.host_surface_index,
            )
    elif ct == "intersection":
        out["points"] = [_tf_apply_pt(tf, p) for p in (curve.get("points") or ())]
    if isinstance(curve.get("sp_surface"), dict):
        out["sp_surface"] = _transform_surface_dict(curve["sp_surface"], tf)
    return out


class _StepBuilder:
    """Stateful ISO-10303-21 entity emitter."""

    def __init__(self, scale: float, units: str, schema: str, name: str) -> None:
        self.scale = scale
        self.units = units
        self.schema = schema.upper()
        self.name = name
        self._entities: list[str] = []
        self._pt_cache: dict[tuple[float, float, float], int] = {}
        self._dir_cache: dict[tuple[float, float, float], int] = {}
        self._style_cache: dict[tuple[int, int, int, int], int] = {}
        self._vert_cache: dict[int, int] = {}
        self._surf_cache: dict[int, tuple[int, bool]] = {}
        self._edge_cache: dict[int, tuple[int, int, int, bool]] = {}

    def reset_geom_caches(self) -> None:
        self._vert_cache.clear()
        self._surf_cache.clear()
        self._edge_cache.clear()

    def style_assignment(self, app: Appearance | None) -> int | None:
        if app is None or app.color_rgb is None:
            return None
        r, g, b = (max(0.0, min(1.0, float(c))) for c in app.color_rgb)
        t = float(app.transparency) if (app.transparency is not None and app.transparency > 1e-6) else 0.0
        t = max(0.0, min(1.0, t))
        key = (round(r * 1e6), round(g * 1e6), round(b * 1e6), round(t * 1e6))
        if key in self._style_cache:
            return self._style_cache[key]
        cid = self.emit(f"COLOUR_RGB('',{_step_real(r)},{_step_real(g)},{_step_real(b)})")
        fasc = self.emit(f"FILL_AREA_STYLE_COLOUR('',#{cid})")
        fas = self.emit(f"FILL_AREA_STYLE('',(#{fasc}))")
        ssfa = self.emit(f"SURFACE_STYLE_FILL_AREA(#{fas})")
        if t > 1e-6:
            sst = self.emit(f"SURFACE_STYLE_TRANSPARENT({_step_real(t)})")
            ssr = self.emit(f"SURFACE_STYLE_RENDERING_WITH_PROPERTIES(.CONSTANT_SHADING.,#{cid},(#{sst}))")
            sss = self.emit(f"SURFACE_SIDE_STYLE('',(#{ssfa},#{ssr}))")
        else:
            sss = self.emit(f"SURFACE_SIDE_STYLE('',(#{ssfa}))")
        ssu = self.emit(f"SURFACE_STYLE_USAGE(.BOTH.,#{sss})")
        psa = self.emit(f"PRESENTATION_STYLE_ASSIGNMENT((#{ssu}))")
        self._style_cache[key] = psa
        return psa

    def emit_styled_item(self, target_id: int, app: Appearance | None, styled_ids: list[int]) -> None:
        psa = self.style_assignment(app)
        if psa is not None:
            styled_ids.append(self.emit(f"STYLED_ITEM('',(#{psa}),#{target_id})"))

    def emit_presentation_if_any(self, styled_ids: list[int], geom_ctx: int) -> None:
        if styled_ids:
            items_str = ",".join(f"#{i}" for i in styled_ids)
            self.emit(f"MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',({items_str}),#{geom_ctx})")

    def emit(self, body: str) -> int:
        eid = len(self._entities) + 1
        self._entities.append(f"#{eid}={body};")
        return eid

    def point(self, pt: tuple[float, float, float], *, scaled: bool = True) -> int:
        f = self.scale if scaled else 1.0
        xyz = (round(pt[0] * f, 12), round(pt[1] * f, 12), round(pt[2] * f, 12))
        if xyz in self._pt_cache:
            return self._pt_cache[xyz]
        eid = self.emit(
            f"CARTESIAN_POINT('',({_step_real(xyz[0])},{_step_real(xyz[1])},{_step_real(xyz[2])}))"
        )
        self._pt_cache[xyz] = eid
        return eid

    def direction(self, v: tuple[float, float, float]) -> int:
        n = _v_norm(v)
        key = (round(n[0], 12), round(n[1], 12), round(n[2], 12))
        if key in self._dir_cache:
            return self._dir_cache[key]
        eid = self.emit(
            f"DIRECTION('',({_step_real(key[0])},{_step_real(key[1])},{_step_real(key[2])}))"
        )
        self._dir_cache[key] = eid
        return eid

    def vector(self, v: tuple[float, float, float], mag: float = 1.0) -> int:
        d = self.direction(v)
        return self.emit(f"VECTOR('',#{d},{_step_real(mag)})")

    def axis2(
        self,
        origin: tuple[float, float, float],
        z_dir: tuple[float, float, float],
        x_dir: tuple[float, float, float],
    ) -> int:
        z, x = _ortho_frame(z_dir, x_dir)
        p = self.point(origin)
        dz = self.direction(z)
        dx = self.direction(x)
        return self.emit(f"AXIS2_PLACEMENT_3D('',#{p},#{dz},#{dx})")

    def axis1(self, origin: tuple[float, float, float], z_dir: tuple[float, float, float]) -> int:
        p = self.point(origin)
        dz = self.direction(z_dir)
        return self.emit(f"AXIS1_PLACEMENT('',#{p},#{dz})")

    def emit_nurbs_curve(self, nc: NurbsCurve) -> int:
        pts_ids = [self.point(p) for p in nc.poles]
        pts_str = ",".join(f"#{i}" for i in pts_ids)
        m_str = ",".join(str(int(m)) for m in nc.multiplicities)
        k_str = ",".join(_step_real(k) for k in nc.knots)
        if nc.rational and nc.weights and len(nc.weights) == len(pts_ids):
            w_str = ",".join(_step_real(w) for w in nc.weights)
            return self.emit(
                "("
                "BOUNDED_CURVE() "
                f"B_SPLINE_CURVE({nc.degree},({pts_str}),.UNSPECIFIED.,{_step_bool(nc.closed)},.F.) "
                f"B_SPLINE_CURVE_WITH_KNOTS(({m_str}),({k_str}),.UNSPECIFIED.) "
                "CURVE() GEOMETRIC_REPRESENTATION_ITEM() "
                f"RATIONAL_B_SPLINE_CURVE(({w_str})) "
                "REPRESENTATION_ITEM('')"
                ")"
            )
        return self.emit(
            f"B_SPLINE_CURVE_WITH_KNOTS('',{nc.degree},({pts_str}),.UNSPECIFIED.,"
            f"{_step_bool(nc.closed)},.F.,({m_str}),({k_str}),.UNSPECIFIED.)"
        )

    def emit_nurbs_surface(self, ns: NurbsSurface) -> int:
        rows = []
        for row in ns.poles:
            row_ids = ",".join(f"#{self.point(p)}" for p in row)
            rows.append(f"({row_ids})")
        grid_str = ",".join(rows)
        um_str = ",".join(str(int(m)) for m in ns.u_mults)
        vm_str = ",".join(str(int(m)) for m in ns.v_mults)
        uk_str = ",".join(_step_real(k) for k in ns.u_knots)
        vk_str = ",".join(_step_real(k) for k in ns.v_knots)

        if ns.rational and ns.weights and len(ns.weights) == len(ns.poles):
            w_rows = ["(" + ",".join(_step_real(w) for w in row) + ")" for row in ns.weights]
            w_str = ",".join(w_rows)
            return self.emit(
                "("
                "BOUNDED_SURFACE() "
                f"B_SPLINE_SURFACE({ns.u_degree},{ns.v_degree},({grid_str}),.UNSPECIFIED.,"
                f"{_step_bool(ns.u_closed)},{_step_bool(ns.v_closed)},.F.) "
                f"B_SPLINE_SURFACE_WITH_KNOTS(({um_str}),({vm_str}),({uk_str}),({vk_str}),.UNSPECIFIED.) "
                "GEOMETRIC_REPRESENTATION_ITEM() "
                f"RATIONAL_B_SPLINE_SURFACE(({w_str})) "
                "REPRESENTATION_ITEM('') SURFACE()"
                ")"
            )
        return self.emit(
            f"B_SPLINE_SURFACE_WITH_KNOTS('',{ns.u_degree},{ns.v_degree},({grid_str}),"
            f".UNSPECIFIED.,{_step_bool(ns.u_closed)},{_step_bool(ns.v_closed)},.F.,"
            f"({um_str}),({vm_str}),({uk_str}),({vk_str}),.UNSPECIFIED.)"
        )

    def emit_curve_geom(self, curve: dict[str, Any]) -> tuple[int, tuple[float, float, float] | None]:
        """Emit STEP curve entity and return (entity_id, default_point_on_curve)."""
        ct = curve.get("type", "unknown")
        if ct == "line":
            o = curve["origin"]
            d = curve["dir"]
            p = self.point(o)
            v = self.vector(d, 1.0)
            return self.emit(f"LINE('',#{p},#{v})"), o
        if ct == "circle":
            c = curve["centre"]
            ax = self.axis2(c, curve["normal"], curve["x_axis"])
            r = float(curve["radius"]) * self.scale
            _, x = _ortho_frame(curve["normal"], curve["x_axis"])
            raw_r = float(curve["radius"])
            seam = (c[0] + raw_r * x[0], c[1] + raw_r * x[1], c[2] + raw_r * x[2])
            return self.emit(f"CIRCLE('',#{ax},{_step_real(r)})"), seam
        if ct == "ellipse":
            c = curve["centre"]
            ax = self.axis2(c, curve["normal"], curve["x_axis"])
            rmaj = float(curve["major_radius"]) * self.scale
            rmin = float(curve["minor_radius"]) * self.scale
            _, x = _ortho_frame(curve["normal"], curve["x_axis"])
            raw_r = float(curve["major_radius"])
            seam = (c[0] + raw_r * x[0], c[1] + raw_r * x[1], c[2] + raw_r * x[2])
            return self.emit(f"ELLIPSE('',#{ax},{_step_real(rmaj)},{_step_real(rmin)})"), seam
        if ct == "nurbs":
            nc: NurbsCurve = curve["nurbs"]
            if nc.is_pcurve:
                host = curve.get("sp_surface")
                if host is not None and host.get("type") == "plane" and not nc.rational:
                    # Exact affine lift on plane preserves degree, knots, and weights
                    poles_3d = [_eval_surface_pt(host, u, v) for u, v in nc.poles_2d]
                    lifted = NurbsCurve(
                        degree=nc.degree,
                        poles=poles_3d,
                        knots=nc.knots,
                        multiplicities=nc.multiplicities,
                        weights=nc.weights,
                        closed=nc.closed,
                        rational=nc.rational,
                    )
                    return self.emit_nurbs_curve(lifted), poles_3d[0] if poles_3d else (0.0, 0.0, 0.0)
                # General 2D p-curve lift onto host surface
                U = _expand_knots(nc.knots, nc.multiplicities)
                t0, t1 = (U[0], U[-1]) if U else (0.0, 1.0)
                nsamp = max(17, 2 * len(nc.poles_2d) + 1)
                sampled_3d: list[tuple[float, float, float]] = []
                for k in range(nsamp):
                    t = t0 + (t1 - t0) * k / (nsamp - 1)
                    uv = _eval_bspline(t, nc.degree, nc.knots, nc.multiplicities, nc.poles_2d, nc.weights)
                    sampled_3d.append(_eval_surface_pt(host or {"type": "plane"}, uv[0], uv[1]))
                lifted = _interpolate_cubic_bspline(sampled_3d)
                lifted.closed = nc.closed
                return self.emit_nurbs_curve(lifted), sampled_3d[0]
            return self.emit_nurbs_curve(nc), nc.poles[0] if nc.poles else (0.0, 0.0, 0.0)
        if ct == "intersection":
            pts = curve.get("points") or []
            if len(pts) >= 2:
                interp = _interpolate_cubic_bspline(pts)
                return self.emit_nurbs_curve(interp), pts[0]
        # Fallback placeholder line
        p = self.point((0.0, 0.0, 0.0))
        v = self.vector((1.0, 0.0, 0.0), 1.0)
        return self.emit(f"LINE('',#{p},#{v})"), (0.0, 0.0, 0.0)

    def emit_surface(self, surf_idx: int | None, surf: dict[str, Any]) -> tuple[int, bool]:
        """Emit STEP surface entity and return (entity_id, flip_face_sense)."""
        if surf_idx is not None and surf_idx in self._surf_cache:
            return self._surf_cache[surf_idx]
        st = surf.get("type", "unknown")
        flip = False
        if st == "plane":
            ax = self.axis2(surf["origin"], surf["normal"], surf["x_axis"])
            sid = self.emit(f"PLANE('',#{ax})")
        elif st == "cylinder":
            ax = self.axis2(surf["origin"], surf["axis"], surf["x_axis"])
            sid = self.emit(f"CYLINDRICAL_SURFACE('',#{ax},{_step_real(surf['radius'] * self.scale)})")
        elif st == "cone":
            # Parasolid CONE axis points toward apex (-v*A expands radius).
            # Negating axis aligns STEP CONICAL_SURFACE +Z away from apex and flips natural normal.
            a = surf["axis"]
            neg_a = (-a[0], -a[1], -a[2])
            ax = self.axis2(surf["origin"], neg_a, surf["x_axis"])
            ha = math.atan2(abs(surf["sin_half_angle"]), surf["cos_half_angle"])
            sid = self.emit(
                f"CONICAL_SURFACE('',#{ax},{_step_real(surf['radius'] * self.scale)},{_step_real(ha)})"
            )
            flip = True
        elif st == "sphere":
            ax = self.axis2(surf["centre"], surf["axis"], surf["x_axis"])
            sid = self.emit(f"SPHERICAL_SURFACE('',#{ax},{_step_real(surf['radius'] * self.scale)})")
        elif st == "torus":
            ax = self.axis2(surf["centre"], surf["axis"], surf["x_axis"])
            sid = self.emit(
                f"TOROIDAL_SURFACE('',#{ax},{_step_real(surf['major_radius'] * self.scale)},"
                f"{_step_real(surf['minor_radius'] * self.scale)})"
            )
        elif st == "nurbs" and isinstance(surf.get("nurbs"), NurbsSurface):
            sid = self.emit_nurbs_surface(surf["nurbs"])
        elif st == "swept" and isinstance(surf.get("profile"), dict):
            prof_id, _ = self.emit_curve_geom(surf["profile"])
            vec = self.vector(surf["direction"], 1.0)
            sid = self.emit(f"SURFACE_OF_LINEAR_EXTRUSION('',#{prof_id},#{vec})")
        elif st == "spun" and isinstance(surf.get("profile"), dict):
            prof_id, _ = self.emit_curve_geom(surf["profile"])
            ax1 = self.axis1(surf["base"], surf["axis"])
            sid = self.emit(f"SURFACE_OF_REVOLUTION('',#{prof_id},#{ax1})")
        elif st == "offset" and isinstance(surf.get("base_surface"), dict):
            base_id, base_flip = self.emit_surface(surf.get("base_surface_index"), surf["base_surface"])
            sid = self.emit(
                f"OFFSET_SURFACE('',#{base_id},{_step_real(surf['offset'] * self.scale)},.F.)"
            )
            flip = base_flip
        else:
            ax = self.axis2((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
            sid = self.emit(f"PLANE('',#{ax})")

        if surf_idx is not None:
            self._surf_cache[surf_idx] = (sid, flip)
        return sid, flip

    def emit_vertex(self, v_idx: int, pt: tuple[float, float, float]) -> int:
        if v_idx in self._vert_cache:
            return self._vert_cache[v_idx]
        p = self.point(pt)
        vid = self.emit(f"VERTEX_POINT('',#{p})")
        self._vert_cache[v_idx] = vid
        return vid

    def emit_edge(
        self,
        edge: Edge,
        first_coedge: Coedge,
        model: BrepModel,
        tf: dict[str, Any] | None = None,
    ) -> tuple[int, int, int, bool]:
        """Emit EDGE_CURVE and return (edge_curve_id, v_start_id, v_end_id, edge_curve_same_sense)."""
        if edge.index in self._edge_cache:
            return self._edge_cache[edge.index]

        curve_dict = _transform_curve_dict(edge.curve, tf) if tf is not None else edge.curve
        cid, fallback_pt = self.emit_curve_geom(curve_dict)

        sv_idx = first_coedge.start_vertex
        ev_idx = first_coedge.end_vertex
        if first_coedge.sense != "+":
            sv_idx, ev_idx = ev_idx, sv_idx

        def _xpt(p: tuple[float, float, float]) -> tuple[float, float, float]:
            return _tf_apply_pt(tf, p) if tf is not None else p

        if sv_idx is not None and sv_idx in model.vertices:
            v1_id = self.emit_vertex(sv_idx, _xpt(model.vertices[sv_idx].point))
        else:
            p_seam = self.point(fallback_pt or (0.0, 0.0, 0.0))
            v1_id = self.emit(f"VERTEX_POINT('',#{p_seam})")

        if ev_idx is not None and ev_idx in model.vertices:
            v2_id = self.emit_vertex(ev_idx, _xpt(model.vertices[ev_idx].point))
        else:
            v2_id = v1_id

        edge_same_sense = edge.sense == "+"
        if edge.curve_type == "intersection" and sv_idx in model.vertices and ev_idx in model.vertices and sv_idx != ev_idx:
            pts = curve_dict.get("points") or []
            if len(pts) >= 2:
                p_start = _xpt(model.vertices[sv_idx].point)
                p_end = _xpt(model.vertices[ev_idx].point)
                d_fwd = sum((pts[0][k] - p_start[k]) ** 2 for k in range(3)) + sum((pts[-1][k] - p_end[k]) ** 2 for k in range(3))
                d_rev = sum((pts[-1][k] - p_start[k]) ** 2 for k in range(3)) + sum((pts[0][k] - p_end[k]) ** 2 for k in range(3))
                edge_same_sense = d_fwd <= d_rev
        ec_id = self.emit(f"EDGE_CURVE('',#{v1_id},#{v2_id},#{cid},{_step_bool(edge_same_sense)})")
        res = (ec_id, v1_id, v2_id, edge_same_sense)
        self._edge_cache[edge.index] = res
        return res

    def emit_body_product(
        self,
        prod_name: str,
        shells: list[tuple[int | None, list[Face], int, Appearance | None]],
        model: BrepModel,
        first_coedge_for_edge: dict[int, Coedge],
        *,
        tf: dict[str, Any] | None = None,
        override_app: Appearance | None = None,
        prod_ctx: int,
        pdc: int,
        geom_ctx: int,
        root_ax: int,
    ) -> tuple[int, int]:
        self.reset_geom_caches()
        sn = _step_str(prod_name)
        prod = self.emit(f"PRODUCT({sn},{sn},'',(#{prod_ctx}))")
        pdf = self.emit(f"PRODUCT_DEFINITION_FORMATION('','',#{prod})")
        pd = self.emit(f"PRODUCT_DEFINITION('design','',#{pdf},#{pdc})")
        pds = self.emit(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")

        det_neg = tf is not None and _tf_det(tf) < 0.0
        solid_items: list[int] = []
        sheet_items: list[int] = []
        styled_items: list[int] = []
        num_shells = len(shells)

        for _sh_idx, faces_in_shell, btype, body_app in shells:
            face_ids: list[int] = []
            edge_use_count: dict[int, int] = {}
            for f in faces_in_shell:
                surf_dict = _transform_surface_dict(f.surface, tf) if tf is not None else f.surface
                sid, surf_flip = self.emit_surface(f.surface_index, surf_dict)
                outer_idx = _pick_outer_loop_index(f, model)
                bound_ids: list[int] = []
                for li, lp in enumerate(f.loops):
                    oe_ids: list[int] = []
                    for ce in lp.coedges:
                        edge = model.edges.get(ce.edge_index)
                        if edge is None:
                            continue
                        edge_use_count[edge.index] = edge_use_count.get(edge.index, 0) + 1
                        ref_ce = first_coedge_for_edge[edge.index]
                        ec_id, _, _, _ = self.emit_edge(edge, ref_ce, model, tf=tf)
                        oe_id = self.emit(
                            f"ORIENTED_EDGE('',*,*,#{ec_id},{_step_bool(ce.sense == '+')})"
                        )
                        oe_ids.append(oe_id)
                    if not oe_ids:
                        continue
                    loop_id = self.emit("EDGE_LOOP('',(" + ",".join(f"#{i}" for i in oe_ids) + "))")
                    btype_str = "FACE_OUTER_BOUND" if li == outer_idx else "FACE_BOUND"
                    bound_ids.append(self.emit(f"{btype_str}('',#{loop_id},.T.)"))

                if not bound_ids:
                    continue
                face_sense = f.sense == "+"
                if surf_flip:
                    face_sense = not face_sense
                if det_neg:
                    face_sense = not face_sense
                af_id = self.emit(
                    f"ADVANCED_FACE('',({','.join(f'#{i}' for i in bound_ids)}),#{sid},{_step_bool(face_sense)})"
                )
                self.emit_styled_item(af_id, f.appearance, styled_items)
                face_ids.append(af_id)

            if not face_ids:
                continue
            is_closed = bool(face_ids) and all(c >= 2 for c in edge_use_count.values()) and btype != 2
            faces_str = ",".join(f"#{i}" for i in face_ids)
            eff_app = body_app.inherit_from(override_app) if (body_app and override_app) else (body_app or override_app)
            if is_closed:
                shell = self.emit(f"CLOSED_SHELL('',({faces_str}))")
                item_name = f"{prod_name}_{len(solid_items) + 1}" if num_shells > 1 else prod_name
                msb = self.emit(f"MANIFOLD_SOLID_BREP({_step_str(item_name)},#{shell})")
                self.emit_styled_item(msb, eff_app, styled_items)
                solid_items.append(msb)
            else:
                shell = self.emit(f"OPEN_SHELL('',({faces_str}))")
                sbsm = self.emit(f"SHELL_BASED_SURFACE_MODEL('',(#{shell}))")
                self.emit_styled_item(sbsm, eff_app, styled_items)
                sheet_items.append(sbsm)

        all_items = solid_items + sheet_items
        items_str = ",".join(f"#{i}" for i in all_items + [root_ax])
        if solid_items and not sheet_items:
            rep = self.emit(f"ADVANCED_BREP_SHAPE_REPRESENTATION('',({items_str}),#{geom_ctx})")
        elif sheet_items and not solid_items:
            rep = self.emit(f"MANIFOLD_SURFACE_SHAPE_REPRESENTATION('',({items_str}),#{geom_ctx})")
        else:
            rep = self.emit(f"SHAPE_REPRESENTATION('',({items_str}),#{geom_ctx})")
        self.emit(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")
        self.emit_presentation_if_any(styled_items, geom_ctx)
        return pd, rep

    def _placed_axis(self, tf: dict[str, Any] | None, root_ax: int) -> int:
        if not tf or _tf_is_identity(tf):
            return root_ax
        r = tf.get("rotation_matrix") or [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        if len(r) < 9:
            r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        t = tf.get("translation") or (0.0, 0.0, 0.0)
        x_col = (float(r[0]), float(r[3]), float(r[6]))
        z_col = (float(r[2]), float(r[5]), float(r[8]))
        return self.axis2((float(t[0]), float(t[1]), float(t[2])), z_col, x_col)

    def _emit_assembly_node(
        self,
        asm: AssemblyInfo,
        seq: int,
        asm_map: dict[int, AssemblyInfo],
        body_map: dict[int, dict[str, Any]],
        inst_map: dict[int, dict[str, Any]],
        body_shells_map: dict[int, list[tuple[int | None, list[Face], int, Appearance | None]]],
        model: BrepModel,
        first_coedge_for_edge: dict[int, Coedge],
        *,
        prod_ctx: int,
        pdc: int,
        geom_ctx: int,
        root_ax: int,
        asm_cache: dict[int, tuple[int, int]],
        body_cache: dict[int, tuple[int, int]],
        visiting: set[int],
        occ_counter: list[int],
    ) -> tuple[int, int] | None:
        if asm.index in asm_cache:
            return asm_cache[asm.index]
        if asm.index in visiting:
            return None
        visiting.add(asm.index)

        child_placements: list[tuple[tuple[int, int], int, str]] = []
        for local_i, inst_idx in enumerate(asm.instance_indices):
            inst = inst_map.get(inst_idx)
            if inst is None:
                continue
            part_idx = inst.get("part")
            if part_idx is None:
                continue
            tf = inst.get("transform")
            occ_name = inst.get("name") or (
                f"{body_map[part_idx].get('name')}:{local_i + 1}"
                if part_idx in body_map and body_map[part_idx].get("name")
                else f"Component_{local_i + 1}"
            )
            if part_idx in body_map:
                shells = body_shells_map.get(part_idx) or []
                if not shells:
                    continue
                if _tf_is_rigid(tf):
                    if part_idx not in body_cache:
                        bname = body_map[part_idx].get("name") or f"{self.name}_part_{part_idx}"
                        body_cache[part_idx] = self.emit_body_product(
                            bname,
                            shells,
                            model,
                            first_coedge_for_edge,
                            prod_ctx=prod_ctx,
                            pdc=pdc,
                            geom_ctx=geom_ctx,
                            root_ax=root_ax,
                        )
                    child_placements.append((body_cache[part_idx], self._placed_axis(tf, root_ax), occ_name))
                else:
                    bname = f"{body_map[part_idx].get('name') or self.name}_inst_{inst_idx}"
                    c = self.emit_body_product(
                        bname,
                        shells,
                        model,
                        first_coedge_for_edge,
                        tf=tf,
                        override_app=inst.get("appearance"),
                        prod_ctx=prod_ctx,
                        pdc=pdc,
                        geom_ctx=geom_ctx,
                        root_ax=root_ax,
                    )
                    child_placements.append((c, root_ax, occ_name))
            elif part_idx in asm_map:
                sub = self._emit_assembly_node(
                    asm_map[part_idx],
                    seq + len(child_placements) + 1,
                    asm_map,
                    body_map,
                    inst_map,
                    body_shells_map,
                    model,
                    first_coedge_for_edge,
                    prod_ctx=prod_ctx,
                    pdc=pdc,
                    geom_ctx=geom_ctx,
                    root_ax=root_ax,
                    asm_cache=asm_cache,
                    body_cache=body_cache,
                    visiting=visiting,
                    occ_counter=occ_counter,
                )
                if sub is not None:
                    child_placements.append((sub, self._placed_axis(tf, root_ax), occ_name))

        visiting.discard(asm.index)
        if not child_placements:
            return None

        asm_name = asm.name or (self.name if seq == 0 else f"{self.name}_asm_{asm.index}")
        sa = _step_str(asm_name)
        prod = self.emit(f"PRODUCT({sa},{sa},'',(#{prod_ctx}))")
        pdf = self.emit(f"PRODUCT_DEFINITION_FORMATION('','',#{prod})")
        pd = self.emit(f"PRODUCT_DEFINITION('design','',#{pdf},#{pdc})")
        pds = self.emit(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
        rep = self.emit(f"SHAPE_REPRESENTATION({sa},(#{root_ax}),#{geom_ctx})")
        self.emit(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")

        for (child_pd, child_rep), placed_ax, occ_name in child_placements:
            occ_counter[0] += 1
            idt = self.emit(f"ITEM_DEFINED_TRANSFORMATION('','',#{root_ax},#{placed_ax})")
            nauo = self.emit(
                f"NEXT_ASSEMBLY_USAGE_OCCURRENCE({_step_str(f'NAUO_{occ_counter[0]}')},{_step_str(occ_name)},'',#{pd},#{child_pd},$)"
            )
            nauo_pds = self.emit(f"PRODUCT_DEFINITION_SHAPE('','',#{nauo})")
            rel = self.emit(
                f"(REPRESENTATION_RELATIONSHIP('','',#{child_rep},#{rep}) "
                f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{idt}) SHAPE_REPRESENTATION_RELATIONSHIP())"
            )
            self.emit(f"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{rel},#{nauo_pds})")

        asm_cache[asm.index] = (pd, rep)
        return pd, rep

    def build(self, model: BrepModel) -> str:
        app_ctx = self.emit(
            "APPLICATION_CONTEXT('core data for automotive mechanical design processes')"
            if self.schema == "AP214"
            else "APPLICATION_CONTEXT('managed model based 3d engineering')"
        )
        self.emit(
            f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{app_ctx})"
            if self.schema == "AP214"
            else f"APPLICATION_PROTOCOL_DEFINITION('international standard','ap242_managed_model_based_3d_engineering',2014,#{app_ctx})"
        )
        prod_ctx = self.emit(f"PRODUCT_CONTEXT('',#{app_ctx},'mechanical')")
        pdc = self.emit(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app_ctx},'design')")

        if self.units == "mm":
            len_u = self.emit("(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.))")
        else:
            len_u = self.emit("(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.))")
        ang_u = self.emit("(NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.))")
        sol_u = self.emit("(NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT())")
        unc = self.emit(
            f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-6),#{len_u},'distance_accuracy_value','confusion accuracy')"
        )
        geom_ctx = self.emit(
            f"(GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc})) "
            f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{len_u},#{ang_u},#{sol_u})) REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY'))"
        )

        root_ax = self.axis2((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

        first_coedge_for_edge: dict[int, Coedge] = {}
        for f in model.faces.values():
            for lp in f.loops:
                for ce in lp.coedges:
                    if ce.edge_index not in first_coedge_for_edge:
                        first_coedge_for_edge[ce.edge_index] = ce

        shell_faces: dict[int | None, list[Face]] = {}
        for f in model.faces.values():
            shell_faces.setdefault(f.shell_index, []).append(f)

        emitted_assembly = False
        if model.assemblies and model.instances:
            asm_map = {a.index: a for a in model.assemblies}
            body_map = {b["index"]: b for b in model.bodies}
            inst_map = {i["index"]: i for i in model.instances}
            referenced_asms = {i["part"] for i in model.instances if i.get("part") in asm_map}

            body_shells_map: dict[int, list[tuple[int | None, list[Face], int, Appearance | None]]] = {}
            for b in model.bodies:
                bt = int(b["body_type"]) if b.get("body_type") is not None else 1
                entries = [
                    (sh_i, shell_faces[sh_i], bt, b.get("appearance"))
                    for sh_i in b.get("shell_indices", [])
                    if sh_i in shell_faces
                ]
                if entries:
                    body_shells_map[b["index"]] = entries

            asm_cache: dict[int, tuple[int, int]] = {}
            body_cache: dict[int, tuple[int, int]] = {}
            visiting: set[int] = set()
            occ_counter = [0]

            roots = [a for a in model.assemblies if a.index not in referenced_asms] or model.assemblies
            for seq, asm in enumerate(roots):
                if (
                    self._emit_assembly_node(
                        asm,
                        seq,
                        asm_map,
                        body_map,
                        inst_map,
                        body_shells_map,
                        model,
                        first_coedge_for_edge,
                        prod_ctx=prod_ctx,
                        pdc=pdc,
                        geom_ctx=geom_ctx,
                        root_ax=root_ax,
                        asm_cache=asm_cache,
                        body_cache=body_cache,
                        visiting=visiting,
                        occ_counter=occ_counter,
                    )
                    is not None
                ):
                    emitted_assembly = True

        if not emitted_assembly:
            shell_meta: dict[int | None, tuple[int, Appearance | None]] = {}
            for b in model.bodies:
                bt = int(b["body_type"]) if b.get("body_type") is not None else 1
                for sh_i in b.get("shell_indices", []):
                    shell_meta.setdefault(sh_i, (bt, b.get("appearance")))
            shells = [
                (sh_idx, fs, *shell_meta.get(sh_idx, (1, None)))
                for sh_idx, fs in shell_faces.items()
            ]
            top_name = (
                model.bodies[0].get("name") or self.name
                if len(model.bodies) == 1
                else self.name
            )
            self.emit_body_product(
                top_name,
                shells,
                model,
                first_coedge_for_edge,
                prod_ctx=prod_ctx,
                pdc=pdc,
                geom_ctx=geom_ctx,
                root_ax=root_ax,
            )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        schema_str = (
            "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }"
            if self.schema == "AP214"
            else "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }"
        )
        header_lines = [
            "ISO-10303-21;",
            "HEADER;",
            "FILE_DESCRIPTION(('xtopen STEP AP{0} export'),'2;1');".format(
                "214" if self.schema == "AP214" else "242"
            ),
            f"FILE_NAME({_step_str(self.name + '.step')},'{now}',('xtopen'),(''),'xtopen','','');",
            f"FILE_SCHEMA(('{schema_str}'));",
            "ENDSEC;",
            "DATA;",
        ]
        return "\n".join(header_lines + self._entities + ["ENDSEC;", "END-ISO-10303-21;", ""])


def _loop_span(loop: Loop, model: BrepModel) -> float:
    pts: list[tuple[float, float, float]] = []
    for ce in loop.coedges:
        if ce.start_vertex is not None and ce.start_vertex in model.vertices:
            pts.append(model.vertices[ce.start_vertex].point)
        edge = model.edges.get(ce.edge_index)
        if edge is not None and edge.curve_type in ("circle", "ellipse"):
            r = float(edge.curve.get("radius") or edge.curve.get("major_radius") or 0.0)
            return r
    if not pts:
        return 0.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    return (max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2


def _pick_outer_loop_index(face: Face, model: BrepModel) -> int:
    if len(face.loops) <= 1:
        return 0
    best_i = 0
    best_span = -1.0
    for i, lp in enumerate(face.loops):
        sp = _loop_span(lp, model)
        if sp > best_span:
            best_span = sp
            best_i = i
    return best_i


def dumps_step(
    source: ParseResult | BrepModel,
    *,
    schema: str = "AP214",
    units: str = "mm",
    name: str = "xtopen_model",
) -> str:
    """Convert a ParseResult or BrepModel to an ISO-10303-21 STEP string."""
    if units not in ("mm", "m"):
        raise ValueError(f"Unsupported units {units!r}; expected 'mm' or 'm'")
    if schema.upper() not in ("AP214", "AP242"):
        raise ValueError(f"Unsupported STEP schema {schema!r}; expected 'AP214' or 'AP242'")
    model = extract_brep(source) if isinstance(source, ParseResult) else source
    scale = 1000.0 if units == "mm" else 1.0
    return _StepBuilder(scale=scale, units=units, schema=schema, name=name).build(model)


def export_step(
    source: ParseResult | BrepModel,
    path: str | Path,
    *,
    schema: str = "AP214",
    units: str = "mm",
    name: str | None = None,
) -> Path:
    """Write an ISO-10303-21 STEP file for the given ParseResult or BrepModel."""
    out_path = Path(path)
    step_text = dumps_step(
        source,
        schema=schema,
        units=units,
        name=name or out_path.stem or "xtopen_model",
    )
    out_path.write_text(step_text, encoding="ascii")
    return out_path
