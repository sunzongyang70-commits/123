"""
Topology Analysis — Phase 1.2A
Deterministic computation of mesh topology from a triangle soup.

Computes:
  - vertex/edge/face counts
  - bounding box, extents, diagonal
  - connected components (BFS over shared edges)
  - per-component statistics
  - boundary edge count + ordered boundary loops
  - non-manifold edge detection
  - watertight flag
  - Euler characteristic (V - E + F)

No external dependencies.
"""

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .stl_ingest import STLIngestResult, Triangle

# Canonical edge: always (min_vertex_id, max_vertex_id)
Edge = Tuple[int, int]
Vertex3 = Tuple[float, float, float]


# ── vertex deduplication ──────────────────────────────────────────────────────

def _deduplicate_vertices(
    triangles: List[Triangle], weld_tolerance: float
) -> Tuple[List[Vertex3], List[Tuple[int, int, int]]]:
    """
    Return (vertices, faces).
    When weld_tolerance == 0.0 exact coordinate matching is used.
    When weld_tolerance > 0.0 a grid-based bucket merge is used.
    """
    vertex_map: Dict[Any, int] = {}
    vertices: List[Vertex3] = []
    faces: List[Tuple[int, int, int]] = []

    def _key_exact(v: Vertex3) -> Any:
        return v  # (float, float, float) — hashable tuple

    def _key_grid(v: Vertex3) -> Any:
        t = weld_tolerance
        return (
            math.floor(v[0] / t),
            math.floor(v[1] / t),
            math.floor(v[2] / t),
        )

    key_fn = _key_exact if weld_tolerance == 0.0 else _key_grid

    for tri in triangles:
        _, v0, v1, v2 = tri
        face_ids = []
        for v in (v0, v1, v2):
            k = key_fn(v)
            if k not in vertex_map:
                vertex_map[k] = len(vertices)
                vertices.append(v)
            face_ids.append(vertex_map[k])
        if len(set(face_ids)) == 3:  # skip degenerate triangles
            faces.append((face_ids[0], face_ids[1], face_ids[2]))

    return vertices, faces


# ── bounding box ──────────────────────────────────────────────────────────────

def _bbox(vertices: List[Vertex3]) -> Dict[str, Any]:
    if not vertices:
        return {
            "min": [0.0, 0.0, 0.0],
            "max": [0.0, 0.0, 0.0],
            "extents": [0.0, 0.0, 0.0],
            "diagonal": 0.0,
        }
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    mn = [min(xs), min(ys), min(zs)]
    mx = [max(xs), max(ys), max(zs)]
    ext = [mx[i] - mn[i] for i in range(3)]
    diag = math.sqrt(sum(e * e for e in ext))
    return {"min": mn, "max": mx, "extents": ext, "diagonal": diag}


# ── edge / adjacency structures ───────────────────────────────────────────────

def _build_edge_table(
    faces: List[Tuple[int, int, int]]
) -> Tuple[Dict[Edge, List[int]], Dict[int, List[int]]]:
    """
    Returns:
        edge_to_faces : edge → [face_ids]
        face_to_faces : face_id → [adjacent face_ids]
    """
    edge_to_faces: Dict[Edge, List[int]] = defaultdict(list)
    for fi, (a, b, c) in enumerate(faces):
        for e in ((min(a, b), max(a, b)), (min(b, c), max(b, c)), (min(a, c), max(a, c))):
            edge_to_faces[e].append(fi)

    face_to_faces: Dict[int, List[int]] = defaultdict(list)
    for nbrs in edge_to_faces.values():
        if len(nbrs) == 2:
            fi, fj = nbrs
            face_to_faces[fi].append(fj)
            face_to_faces[fj].append(fi)
    return dict(edge_to_faces), dict(face_to_faces)


# ── connected components ──────────────────────────────────────────────────────

def _connected_components(
    face_count: int,
    face_to_faces: Dict[int, List[int]],
) -> List[List[int]]:
    """BFS; returns list of component face-id lists."""
    visited = [False] * face_count
    components: List[List[int]] = []
    for start in range(face_count):
        if visited[start]:
            continue
        comp: List[int] = []
        q: deque = deque([start])
        visited[start] = True
        while q:
            fi = q.popleft()
            comp.append(fi)
            for nbr in face_to_faces.get(fi, []):
                if not visited[nbr]:
                    visited[nbr] = True
                    q.append(nbr)
        components.append(comp)
    return components


# ── triangle area ─────────────────────────────────────────────────────────────

def _tri_area(v0: Vertex3, v1: Vertex3, v2: Vertex3) -> float:
    ax, ay, az = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    bx, by, bz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


# ── boundary loops ────────────────────────────────────────────────────────────

def _extract_boundary_loops(
    boundary_edges: List[Edge],
    vertices: List[Vertex3],
) -> List[Dict[str, Any]]:
    """
    Deterministically traverse boundary edges and produce loop/chain dicts.
    Sorted by minimum vertex id for stable IDs.
    """
    if not boundary_edges:
        return []

    # Build adjacency for boundary vertices
    adj: Dict[int, List[int]] = defaultdict(list)
    for a, b in boundary_edges:
        adj[a].append(b)
        adj[b].append(a)

    visited_edges: set = set()
    chains: List[List[int]] = []

    def _canonical(a: int, b: int) -> Edge:
        return (min(a, b), max(a, b))

    # Start traversal from the smallest unvisited vertex
    start_candidates = sorted(adj.keys())
    for start in start_candidates:
        # Find an unvisited edge from start
        for nxt in sorted(adj[start]):
            ce = _canonical(start, nxt)
            if ce in visited_edges:
                continue
            # Traverse this chain
            chain = [start]
            visited_edges.add(ce)
            prev, cur = start, nxt
            while True:
                chain.append(cur)
                nbrs = [v for v in sorted(adj[cur]) if _canonical(cur, v) not in visited_edges]
                if not nbrs:
                    break
                # Prefer not going back to prev
                preferred = [v for v in nbrs if v != prev]
                nxt_v = preferred[0] if preferred else nbrs[0]
                visited_edges.add(_canonical(cur, nxt_v))
                prev, cur = cur, nxt_v
                if cur == start:
                    break  # closed loop
            chains.append(chain)

    loops = []
    for idx, chain in enumerate(sorted(chains, key=lambda c: min(c))):
        is_closed = len(chain) >= 3 and chain[0] == chain[-1]
        # perimeter
        perimeter = 0.0
        coords = []
        for vi in chain:
            coords.append(list(vertices[vi]))
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            va, vb = vertices[a], vertices[b]
            perimeter += math.sqrt(sum((vb[j] - va[j]) ** 2 for j in range(3)))

        loop_id = f"BL_{idx + 1:04d}"
        loops.append(
            {
                "id": loop_id,
                "closed": is_closed,
                "vertex_count": len(chain),
                "perimeter": perimeter,
                "orientation": "UNKNOWN",
                "ordered_vertex_ids": chain,
                "ordered_coordinates": coords,
                "status": "CLOSED" if is_closed else "OPEN_OR_BRANCHED",
            }
        )
    return loops


# ── per-component stats ───────────────────────────────────────────────────────

def _component_stats(
    comp_id: int,
    face_ids: List[int],
    faces: List[Tuple[int, int, int]],
    vertices: List[Vertex3],
    edge_to_faces: Dict[Edge, List[int]],
    all_boundary_loop_ids: List[str],
    loop_boundary_edges: Dict[str, List[Edge]],
) -> Dict[str, Any]:
    face_set = set(face_ids)
    # Gather vertices in component
    vset: set = set()
    for fi in face_ids:
        vset.update(faces[fi])
    comp_verts = [vertices[vi] for vi in sorted(vset)]

    # Surface area
    area = sum(_tri_area(vertices[a], vertices[b], vertices[c]) for a, b, c in (faces[fi] for fi in face_ids))

    # Component-level edges
    comp_edges_all: Dict[Edge, int] = {}
    for fi in face_ids:
        a, b, c = faces[fi]
        for e in ((min(a, b), max(a, b)), (min(b, c), max(b, c)), (min(a, c), max(a, c))):
            comp_edges_all[e] = comp_edges_all.get(e, 0) + 1

    edge_count = len(comp_edges_all)
    boundary_edge_count = sum(1 for e, cnt in comp_edges_all.items() if cnt == 1)
    non_manifold_edge_count = sum(
        1 for e in comp_edges_all if len(edge_to_faces.get(e, [])) > 2
    )
    v_count = len(vset)
    f_count = len(face_ids)
    euler = v_count - edge_count + f_count

    watertight = boundary_edge_count == 0 and non_manifold_edge_count == 0

    # Boundary loops belonging to this component
    comp_loop_ids = []
    for lid, ledges in loop_boundary_edges.items():
        for e in ledges:
            if e in comp_edges_all:
                comp_loop_ids.append(lid)
                break

    bb = _bbox(comp_verts)

    return {
        "component_id": comp_id,
        "semantic_role": "UNKNOWN",
        "face_count": f_count,
        "vertex_count": v_count,
        "edge_count": edge_count,
        "bounding_box": bb,
        "surface_area": area,
        "boundary_edge_count": boundary_edge_count,
        "boundary_loop_ids": sorted(set(comp_loop_ids)),
        "non_manifold_edge_count": non_manifold_edge_count,
        "watertight": watertight,
        "euler_characteristic": euler,
    }


# ── main topology function ────────────────────────────────────────────────────

@dataclass
class TopologyResult:
    variant: str  # "raw_exact" | "welded"
    weld_tolerance: float
    vertex_count: int
    face_count: int
    edge_count: int
    bounding_box: Dict[str, Any]
    connected_component_count: int
    components: List[Dict[str, Any]]
    boundary_edge_count: int
    boundary_loops: List[Dict[str, Any]]
    non_manifold_edge_count: int
    watertight: bool
    euler_characteristic: int


def compute_topology(
    ingest_result: STLIngestResult,
    variant: str,
    weld_tolerance: Optional[float] = None,
) -> TopologyResult:
    """
    Compute mesh topology for a single variant ('raw_exact' or 'welded').
    weld_tolerance=None means it will be derived from bbox diagonal.
    """
    if variant == "raw_exact":
        tol = 0.0
    else:
        if weld_tolerance is None:
            # Derive from bbox diagonal of all raw vertices
            all_verts = [v for tri in ingest_result.triangles for v in tri[1:]]
            bb = _bbox(all_verts)
            diag = bb["diagonal"]
            tol = 1e-8 * diag if diag > 0.0 else 0.0
        else:
            tol = weld_tolerance

    vertices, faces = _deduplicate_vertices(ingest_result.triangles, tol)

    bb = _bbox(vertices)
    edge_to_faces, face_to_faces = _build_edge_table(faces)

    boundary_edges = [e for e, flist in edge_to_faces.items() if len(flist) == 1]
    non_manifold_edges = [e for e, flist in edge_to_faces.items() if len(flist) > 2]

    boundary_loops = _extract_boundary_loops(boundary_edges, vertices)

    # Map loop id → edges (for per-component stats)
    loop_boundary_edges: Dict[str, List[Edge]] = {}
    for loop in boundary_loops:
        chain = loop["ordered_vertex_ids"]
        edges = []
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            edges.append((min(a, b), max(a, b)))
        loop_boundary_edges[loop["id"]] = edges

    comp_face_lists = _connected_components(len(faces), face_to_faces)

    components = []
    for cid, face_ids in enumerate(comp_face_lists):
        cs = _component_stats(
            cid,
            face_ids,
            faces,
            vertices,
            edge_to_faces,
            [l["id"] for l in boundary_loops],
            loop_boundary_edges,
        )
        components.append(cs)

    v_count = len(vertices)
    f_count = len(faces)
    e_count = len(edge_to_faces)
    euler = v_count - e_count + f_count
    watertight = len(boundary_edges) == 0 and len(non_manifold_edges) == 0

    return TopologyResult(
        variant=variant,
        weld_tolerance=tol,
        vertex_count=v_count,
        face_count=f_count,
        edge_count=e_count,
        bounding_box=bb,
        connected_component_count=len(comp_face_lists),
        components=components,
        boundary_edge_count=len(boundary_edges),
        boundary_loops=boundary_loops,
        non_manifold_edge_count=len(non_manifold_edges),
        watertight=watertight,
        euler_characteristic=euler,
    )
