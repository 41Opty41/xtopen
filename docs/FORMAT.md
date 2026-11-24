# Parasolid XT Format Reference (Clean-Room Notes)

This document summarizes the structure of Parasolid XT transmit files handled by `xtopen`.

## 1. File Framing & Common Header

All standard `.x_b`, `.x_t`, `.x_p`, and `.x_d` files begin with a readable ASCII header block partitioned into `**PART1;`, `**PART2;`, `**PART3;` key-value entries and terminated by a line starting with `**END_OF_HEADER`.

- `FORMAT=binary` / `FORMAT=text`
- `GUISE=transmit` / `GUISE=transmit_partition`
- `SCH=...` (schema identifier)
- `USFLD_SIZE=...` (user field byte count, typically `0`)

## 2. Binary & Text Stream Preambles

### Binary (`.x_b`, `.x_p`, `.x_d`)

After `**END_OF_HEADER`:

1. 4-byte magic: `PS\x00\x00`
2. Big-endian `int16` length + ASCII modeler string
3. Big-endian `int32` length + ASCII schema name (`SCH_...` for Form-2; `sch_...` for Form-1)
4. Form-2 4-part schema names (`SCH_a_b_c`) include an extra big-endian `int16` `max_node`
5. Big-endian `int32` `user_field_size`

### Text (`.x_t`)

Physical newlines (`\r`, `\n`) are pure line-wrapping continuations (typically 78 columns) and are stripped before tokenizing.

1. Literal `'T'`
2. `<len> <exactly len chars>` modeler string
3. `<len> <exactly len chars>` schema name
4. Optional `<max_node>` (for 4-part `SCH_a_b_c` Form-2 schemas) + `<user_field_size>`

## 3. Field Types & Index Encoding

| Code | Meaning | Binary Size | Text Encoding |
| --- | --- | --- | --- |
| `u` | Unsigned byte / enum | 1 byte | Decimal integer |
| `c` | Signed/raw char (`+`/`-` sense, or string array) | 1 byte per char | Single char or raw varlen char sequence |
| `l` | Logical boolean | 1 byte (`0`/`1`) | `T` / `F` (or `0`/`1`) |
| `n` | Short int | `int16` | Decimal integer |
| `w` | Unsigned short int | `uint16` | Decimal integer |
| `d`, `t` | 32-bit int / tag | `int32` | Decimal integer |
| `p` | Node pointer index | Variable (`i16` or `i16 + u16`) | `0` for null, 1-based integer |
| `f` | IEEE-754 double | `float64` (8 bytes) | Floating-point literal |
| `i` | 2-double interval | 2 x `float64` | 2 floats |
| `v`, `h` | 3-vector / homogenous | 3 x `float64` | 3 floats |
| `b` | Bounding box | 6 x `float64` | 6 floats |

Binary unset sentinels: `UNSET_I = -32764` (`-0x7FFC`), `UNSET_R = -3.14158e13`. In text streams, unset values appear as `?`.

### Pointer Index Encoding (Binary)

- Wire `0` -> `None` (null pointer)
- Wire `1..32767` -> `index = wire - 1`
- Negative `rem < 0`: reads trailing `uint16 q`; `index = q * 32767 + (-rem) - 1`.

## 4. Form-1 vs Form-2 Schema Edits

On the first encounter of node type `T` in a stream:

- `flag == 255`: node layout equals the effective transmitted fields of base schema node `T`.
- Otherwise, `flag` specifies the field count and is followed either by:
  - Edit ops `C` (copy base field), `D` (drop base field), `I <decl>` (insert field), `A <decl>` (append field), terminated by `Z`, OR
  - A full embedded node name/description/field list for extended custom types.

Stream records terminate at node type `1` (`NULLP`).

## 5. Geometry & Topology Conventions

- **Units**: Coordinates and radii in XT are stored in **meters**.
- **Cone (`CONE 52`)**: Axis vector points *toward* the narrower/apex direction (`R(u, v) = P - v*A + ...`), which is the opposite direction of STEP `CONICAL_SURFACE` `+Z`.
- **NURBS (`NURBS_SURF 126`, `NURBS_CURVE 136`)**:
  - Rational curves/surfaces store homogeneous coordinates `[w*x, w*y, w*z, w]` (`vertex_dim=4`).
  - Surface control grids are flattened in `iu * n_v + iv` (`u`-major) order.
  - 2D trimming/parametric curves (`SP_CURVE 137 -> NURBS_CURVE 136`) use `vertex_dim=2` `(u, v)` (or `vertex_dim=3` `(w*u, w*v, w)` when rational) evaluated in the host surface's `(u, v)` domain.
- **Loops & Halfedges**: Each `LOOP` links circularly via `HALFEDGE.forward`; traversing loops keeps face material to the left when viewed from outside the active face normal.
