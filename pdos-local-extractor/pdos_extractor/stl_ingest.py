"""
STL Ingest — Phase 1.2A
Reads binary or ASCII STL files; computes SHA-256; returns raw triangle soup.
No external dependencies beyond the Python standard library.
"""

import hashlib
import os
import struct
from dataclasses import dataclass, field
from typing import List, Tuple

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
    parse_error: str = ""


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
    head = raw[:256].lstrip()
    return head.startswith(b"solid") and b"facet normal" in raw


def _parse_ascii(raw: bytes) -> Tuple[List[Triangle], str]:
    """Return (triangles, error_string). error_string == '' on success."""
    triangles: List[Triangle] = []
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return triangles, f"Decode error: {exc}"

    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if line.startswith("facet normal"):
            parts = line.split()
            try:
                nx, ny, nz = float(parts[2]), float(parts[3]), float(parts[4])
            except (IndexError, ValueError):
                nx, ny, nz = 0.0, 0.0, 0.0
            verts: List[Tuple[float, float, float]] = []
            i += 1
            while i < n and len(verts) < 3:
                vline = lines[i].strip()
                if vline.startswith("vertex"):
                    vp = vline.split()
                    try:
                        verts.append((float(vp[1]), float(vp[2]), float(vp[3])))
                    except (IndexError, ValueError):
                        verts.append((0.0, 0.0, 0.0))
                i += 1
            if len(verts) == 3:
                triangles.append(((nx, ny, nz), verts[0], verts[1], verts[2]))
        else:
            i += 1
    return triangles, ""


def _parse_binary(raw: bytes) -> Tuple[List[Triangle], str]:
    """Return (triangles, error_string). error_string == '' on success."""
    if len(raw) < 84:
        return [], "Binary STL too short (< 84 bytes)"
    # bytes 0-79: header; bytes 80-83: uint32 triangle count
    try:
        tri_count = struct.unpack_from("<I", raw, 80)[0]
    except struct.error as exc:
        return [], f"Cannot read triangle count: {exc}"

    expected_size = 84 + tri_count * 50
    if len(raw) < expected_size:
        return (
            [],
            f"Truncated binary STL: expected {expected_size} bytes, got {len(raw)}",
        )

    triangles: List[Triangle] = []
    offset = 84
    for _ in range(tri_count):
        try:
            vals = struct.unpack_from("<12fH", raw, offset)
        except struct.error as exc:
            return triangles, f"Parse error at offset {offset}: {exc}"
        nx, ny, nz = vals[0], vals[1], vals[2]
        v0 = (vals[3], vals[4], vals[5])
        v1 = (vals[6], vals[7], vals[8])
        v2 = (vals[9], vals[10], vals[11])
        triangles.append(((nx, ny, nz), v0, v1, v2))
        offset += 50
    return triangles, ""


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

    parse_success = error == ""
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
