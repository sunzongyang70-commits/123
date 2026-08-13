"""
STL Ingest — Phase 1.2A
Reads binary or ASCII STL files; computes SHA-256; returns raw triangle soup.
No external dependencies beyond the Python standard library.
"""

import hashlib
import math
import os
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

Triangle = Tuple[
    Tuple[float, float, float],  # normal
    Tuple[float, float, float],  # v0
    Tuple[float, float, float],  # v1
    Tuple[float, float, float],  # v2
]


@dataclass
class STLIngestResult:
    input_path: str
    filename: str
    byte_size: int
    sha256: str
    detected_format: str  # "ASCII" | "BINARY"
    unit_status: str      # always "UNKNOWN"
    triangle_count: int
    triangles: List[Triangle] = field(default_factory=list)
    parse_success: bool = True
    parse_error: Optional[Dict[str, Any]] = None


# ── helpers ──────────────────────────────────────────────────────────────────

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_ascii_stl(raw: bytes) -> bool:
    """
    Heuristic: if the first 256 bytes (stripped) start with b'solid' and the
    file contains b'facet normal' the format is ASCII.
    Binary files can start with 'solid' too, so also require 'facet normal'.
    """
    # A binary STL header is arbitrary and may deliberately start with
    # ``solid`` or contain the words ``facet normal``.  An exact binary record
    # length therefore takes precedence over textual markers.
    if len(raw) >= 84:
        tri_count = struct.unpack_from("<I", raw, 80)[0]
        if 84 + tri_count * 50 == len(raw):
            return False
    head = raw[:256].lstrip().lower()
    lowered = raw.lower()
    return (
        head.startswith(b"solid")
        and b"facet normal" in lowered
        and b"endsolid" in lowered
    )


def _parse_ascii(
    raw: bytes,
) -> Tuple[List[Triangle], Optional[Dict[str, Any]]]:
    """Parse ASCII STL without inventing missing or malformed coordinates.

    Valid facets are collected so callers can diagnose a damaged file, but any
    structural or numeric error makes the complete ingest unsuccessful.  The
    caller must not compute Primary Evidence topology from a partial result.
    """
    triangles: List[Triangle] = []
    text = raw.decode("utf-8", errors="replace")

    lines = text.splitlines()
    errors: List[Dict[str, Any]] = []

    def add_error(line_index: int, reason: str) -> None:
        errors.append({
            "line_number": line_index + 1,
            "reason": reason,
            "raw_line": lines[line_index] if 0 <= line_index < len(lines) else "",
        })

    def parse_finite_triplet(
        parts: List[str], line_index: int, label: str, offset: int
    ) -> Optional[Tuple[float, float, float]]:
        if len(parts) < offset + 3:
            add_error(line_index, f"Malformed {label}: expected 3 coordinates")
            return None
        try:
            values = tuple(float(parts[offset + j]) for j in range(3))
        except ValueError:
            add_error(line_index, f"Malformed {label} coordinate")
            return None
        if not all(math.isfinite(value) for value in values):
            add_error(line_index, f"Non-finite {label} coordinate")
            return None
        return values  # type: ignore[return-value]

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        lowered = line.lower()
        if lowered.startswith("facet normal"):
            facet_line = i
            parts = line.split()
            normal = parse_finite_triplet(parts, i, "facet normal", 2)
            verts: List[Tuple[float, float, float]] = []
            saw_outer_loop = False
            saw_endloop = False
            saw_endfacet = False
            i += 1
            while i < n:
                vline = lines[i].strip()
                vlower = vline.lower()
                if vlower == "outer loop":
                    saw_outer_loop = True
                elif vlower.startswith("vertex"):
                    vp = vline.split()
                    vertex = parse_finite_triplet(vp, i, "vertex", 1)
                    if vertex is not None:
                        verts.append(vertex)
                elif vlower == "endloop":
                    saw_endloop = True
                elif vlower == "endfacet":
                    saw_endfacet = True
                    i += 1
                    break
                elif vlower.startswith("facet normal"):
                    add_error(i, "Nested facet before endfacet")
                    break
                i += 1
            if not saw_outer_loop:
                add_error(facet_line, "Facet is missing outer loop")
            if not saw_endloop:
                add_error(facet_line, "Facet is missing endloop")
            if not saw_endfacet:
                add_error(facet_line, "Facet is missing endfacet")
            if len(verts) != 3:
                add_error(
                    facet_line,
                    f"Facet must contain exactly 3 valid vertices; found {len(verts)}",
                )
            if (
                normal is not None
                and len(verts) == 3
                and saw_outer_loop
                and saw_endloop
                and saw_endfacet
            ):
                triangles.append((normal, verts[0], verts[1], verts[2]))
        else:
            i += 1
    if not triangles and not errors:
        errors.append({
            "line_number": 1,
            "reason": "ASCII STL contains no valid facets",
            "raw_line": lines[0] if lines else "",
        })
    if errors:
        first = dict(errors[0])
        first["error_count"] = len(errors)
        if len(errors) > 1:
            first["additional_errors"] = errors[1:]
        return triangles, first
    return triangles, None


def _parse_binary(
    raw: bytes,
) -> Tuple[List[Triangle], Optional[Dict[str, Any]]]:
    """Return triangles and a structured error, if any."""
    def error(reason: str, offset: int = 0) -> Dict[str, Any]:
        return {"byte_offset": offset, "reason": reason, "raw_line": None}

    if len(raw) < 84:
        return [], error("Binary STL too short (< 84 bytes)")
    # bytes 0-79: header; bytes 80-83: uint32 triangle count
    try:
        tri_count = struct.unpack_from("<I", raw, 80)[0]
    except struct.error as exc:
        return [], error(f"Cannot read triangle count: {exc}", 80)

    expected_size = 84 + tri_count * 50
    if len(raw) < expected_size:
        return (
            [],
            error(
                f"Truncated binary STL: expected {expected_size} bytes, got {len(raw)}",
                len(raw),
            ),
        )

    triangles: List[Triangle] = []
    offset = 84
    for _ in range(tri_count):
        try:
            vals = struct.unpack_from("<12fH", raw, offset)
        except struct.error as exc:
            return triangles, error(f"Parse error: {exc}", offset)
        if not all(math.isfinite(value) for value in vals[:12]):
            return triangles, error("Non-finite binary STL coordinate", offset)
        nx, ny, nz = vals[0], vals[1], vals[2]
        v0 = (vals[3], vals[4], vals[5])
        v1 = (vals[6], vals[7], vals[8])
        v2 = (vals[9], vals[10], vals[11])
        triangles.append(((nx, ny, nz), v0, v1, v2))
        offset += 50
    return triangles, None


# ── public API ────────────────────────────────────────────────────────────────

def ingest(path: str) -> STLIngestResult:
    """
    Read an STL file (binary or ASCII), compute SHA-256, and return a
    fully-populated STLIngestResult.  Unit status is always UNKNOWN.
    Raises FileNotFoundError if the file does not exist.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"STL file not found: {path}")

    byte_size = os.path.getsize(path)
    sha256 = _sha256_file(path)

    with open(path, "rb") as fh:
        raw = fh.read()

    if _is_ascii_stl(raw):
        detected_format = "ASCII"
        triangles, error = _parse_ascii(raw)
    else:
        detected_format = "BINARY"
        triangles, error = _parse_binary(raw)

    parse_success = error is None
    return STLIngestResult(
        input_path=path,
        filename=os.path.basename(path),
        byte_size=byte_size,
        sha256=sha256,
        detected_format=detected_format,
        unit_status="UNKNOWN",
        triangle_count=len(triangles),
        triangles=triangles,
        parse_success=parse_success,
        parse_error=error,
    )
