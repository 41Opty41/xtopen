# Changelog

## [0.2.0] - 2026-11-24

- Added multi-body / multi-shell STEP AP214 & AP242 export (`CLOSED_SHELL` / `OPEN_SHELL` grouping).
- Added 3D B-spline interpolation and orientation alignment for `INTERSECTION (38)` curves (`CHART 40` / `LIMIT 41`).
- Fixed `HALFEDGE (17)` end-vertex / start-vertex convention (`backward.vertex -> vertex`) and `EDGE.curve == 0` fallback.
- Fixed ISO-10303-21 Part-21 real formatting (`1.E-12`) to eliminate syntax warnings in `STEPControl_Reader`.
- Updated `freecad/ImportParasolid.FCMacro` for `PySide6` (FreeCAD 1.0+), `PySide2`, and `PySide`.
- Added multilingual READMEs (EN, RU, ZH-CN, DE, JA, ES), SVG logos, and social preview banner.

## [0.1.0] - 2026-11-24

- Initial clean-room release under MIT License: `.x_b` / `.x_t` / `.x_p` / `.x_d` parser & writer, built-in XT schema, B-rep extraction & validation, native STEP AP214/AP242 exporter, CLI, and optional `occt_bridge`.
