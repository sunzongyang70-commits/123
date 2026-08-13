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
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from itertools import combinations
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
    vertices: List[Vertex3] = []
    faces: List[Tuple[int, int, int]] = []
    if not math.isfinite(weld_tolerance) or weld_tolerance < 0.0:
        raise ValueError("weld_tolerance must be a finite, non-negative float")

    exact_map: Dict[Vertex3, int] = {}
    spatial_hash: Dict[Tuple[int, int, int], List[int]] = defaultdict(list)

    def canonical_vertex_id(v: Vertex3) -> int:
        if weld_tolerance == 0.0:
            existing = exact_map.get(v)
            if existing is not None:
                return existing
            vertex_id = len(vertices)
            vertices.append(v)
            exact_map[v] = vertex_id
            return vertex_id

        tolerance = weld_tolerance
        def cell_index(coordinate: float) -> int:
            ratio = coordinate / tolerance
            if math.isfinite(ratio):
                return math.floor(ratio)
            # A positive subnormal tolerance can overflow float division even
            # though both operands are finite. Decimal is a deterministic,
            # standard-library fallback for this rare CLI edge case.
            precise_ratio = (
                Decimal.from_float(coordinate) / Decimal.from_float(tolerance)
            )
            return int(precise_ratio.to_integral_value(rounding=ROUND_FLOOR))

        cell = tuple(cell_index(coordinate) for coordinate in v)
        candidates: List[Tuple[float, int]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for vertex_id in spatial_hash.get(
                        (cell[0] + dx, cell[1] + dy, cell[2] + dz), []
                    ):
                        canonical = vertices[vertex_id]
                        distance = math.dist(v, canonical)
                        if distance <= tolerance:
                            candidates.append((distance, vertex_id))
        if candidates:
            # Minimum distance first; canonical vertex id resolves exact ties.
            return min(candidates, key=lambda item: (item[0], item[1]))[1]

        vertex_id = len(vertices)
        vertices.append(v)
        spatial_hash[cell].append(vertex_id)
        return vertex_id

    for tri in triangles:
        _, v0, v1, v2 = tri
        face_ids = [canonical_vertex_id(v) for v in (v0, v1, v2)]
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
        # All faces incident to an edge are adjacent, including non-manifold
        # edges with more than two incident faces.
        for fi, fj in combinations(sorted(nbrs), 2):
            face_to_faces[fi].append(fj)
            face_to_faces[fj].append(fi)
    return dict(edge_to_faces), {
        face_id: sorted(set(neighbors))
        for face_id, neighbors in face_to_faces.items()
    }


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
    Classify each connected boundary graph as a closed cycle, open chain, or
    branched/non-simple graph.  Only graphs with a unique traversal are emitted
    as ordered chains; branched graphs retain complete graph-level evidence.
    """
    if not boundary_edges:
        return []

    def _canonical(a: int, b: int) -> Edge:
        return (min(a, b), max(a, b))

    canonical_edges = sorted({_canonical(a, b) for a, b in boundary_edges})
    # Build adjacency from unique canonical edges.
    adj: Dict[int, List[int]] = defaultdict(list)
    for a, b in canonical_edges:
        adj[a].append(b)
        adj[b].append(a)

    # Connected components in the boundary graph.
    unvisited_vertices = set(adj)
    graph_components: List[Tuple[List[int], List[Edge]]] = []
    while unvisited_vertices:
        start = min(unvisited_vertices)
        queue = deque([start])
        component_vertices = set([start])
        unvisited_vertices.remove(start)
        while queue:
            current = queue.popleft()
            for neighbor in sorted(adj[current]):
                if neighbor in unvisited_vertices:
                    unvisited_vertices.remove(neighbor)
                    component_vertices.add(neighbor)
                    queue.append(neighbor)
        component_edges = [
            edge for edge in canonical_edges
            if edge[0] in component_vertices and edge[1] in component_vertices
        ]
        graph_components.append((sorted(component_vertices), component_edges))

    records: List[Dict[str, Any]] = []
    for component_vertices, component_edges in graph_components:
        degrees = {vertex_id: len(adj[vertex_id]) for vertex_id in component_vertices}
        endpoints = sorted(
            vertex_id for vertex_id, degree in degrees.items() if degree == 1
        )
        all_degree_two = all(degree == 2 for degree in degrees.values())
        is_open_chain = (
            len(endpoints) == 2
            and all(degrees[vertex_id] in (1, 2) for vertex_id in component_vertices)
        )

        chain: List[int] = []
        closed = False
        if all_degree_two and len(component_vertices) >= 3:
            closed = True
            start = min(component_vertices)
            previous: Optional[int] = None
            current = start
            chain = [start]
            while True:
                choices = [
                    neighbor for neighbor in sorted(adj[current])
                    if neighbor != previous
                ]
                next_vertex = choices[0]
                if next_vertex == start:
                    chain.append(start)  # explicit closure
                    break
                chain.append(next_vertex)
                previous, current = current, next_vertex
            status = "CLOSED_LOOP"
        elif is_open_chain:
            start = min(endpoints)
            previous = None
            current = start
            chain = [start]
            while current not in endpoints or current == start:
                choices = [
                    neighbor for neighbor in sorted(adj[current])
                    if neighbor != previous
                ]
                if not choices:
                    break
                next_vertex = choices[0]
                chain.append(next_vertex)
                previous, current = current, next_vertex
                if current in endpoints and current != start:
                    break
            status = "OPEN_CHAIN"
        else:
            status = "BRANCHED_BOUNDARY_GRAPH"

        perimeter = 0.0
        for a, b in component_edges:
            va, vb = vertices[a], vertices[b]
            perimeter += math.sqrt(
                sum((vb[axis] - va[axis]) ** 2 for axis in range(3))
            )

        records.append({
            "id": "",  # assigned after deterministic record sorting
            "closed": closed,
            "vertex_count": len(component_vertices),
            "edge_count": len(component_edges),
            "perimeter": perimeter,
            "orientation": "UNKNOWN",
            "ordered_vertex_ids": chain,
            "ordered_coordinates": [list(vertices[vertex_id]) for vertex_id in chain],
            "graph_vertex_ids": component_vertices,
            "graph_coordinates": [
                list(vertices[vertex_id]) for vertex_id in component_vertices
            ],
            "graph_edges": [list(edge) for edge in component_edges],
            "vertex_degrees": {
                str(vertex_id): degrees[vertex_id]
                for vertex_id in component_vertices
            },
            "status": status,
        })

    records.sort(key=lambda record: (
        min(record["graph_vertex_ids"]),
        record["vertex_count"],
        tuple(tuple(coordinate) for coordinate in record["graph_coordinates"]),
    ))
    for index, record in enumerate(records, 1):
        record["id"] = f"BL_{index:04d}"
    return records


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

    watertight = (
        f_count > 0
        and boundary_edge_count == 0
        and non_manifold_edge_count == 0
    )

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
    weld_tolerance_status: str
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
    degenerate_face_count: int


def compute_topology(
    ingest_result: STLIngestResult,
    variant: str,
    weld_tolerance: Optional[float] = None,
) -> TopologyResult:
    """
    Compute mesh topology for a single variant ('raw_exact' or 'welded').
    weld_tolerance=None means it will be derived from bbox diagonal.
    """
    if not ingest_result.parse_success:
        raise ValueError("Cannot compute topology from an unsuccessfully parsed STL")
    if variant not in ("raw_exact", "welded"):
        raise ValueError("variant must be 'raw_exact' or 'welded'")

    if variant == "raw_exact":
        tol = 0.0
        tolerance_status = "OBSERVED"
    else:
        if weld_tolerance is None:
            # Derive from bbox diagonal of all raw vertices
            all_verts = [v for tri in ingest_result.triangles for v in tri[1:]]
            bb = _bbox(all_verts)
            diag = bb["diagonal"]
            tol = 1e-8 * diag if diag > 0.0 else 0.0
            tolerance_status = "DERIVED" if diag > 0.0 else "UNKNOWN"
        else:
            tol = weld_tolerance
            tolerance_status = "OBSERVED"

    if not math.isfinite(tol) or tol < 0.0:
        raise ValueError("weld_tolerance must be a finite, non-negative float")

    vertices, faces = _deduplicate_vertices(ingest_result.triangles, tol)

    bb = _bbox(vertices)
    edge_to_faces, face_to_faces = _build_edge_table(faces)

    boundary_edges = [e for e, flist in edge_to_faces.items() if len(flist) == 1]
    non_manifold_edges = [e for e, flist in edge_to_faces.items() if len(flist) > 2]

    boundary_loops = _extract_boundary_loops(boundary_edges, vertices)

    # Map loop id → edges (for per-component stats)
    loop_boundary_edges: Dict[str, List[Edge]] = {}
    for loop in boundary_loops:
        loop_boundary_edges[loop["id"]] = [
            (edge[0], edge[1]) for edge in loop["graph_edges"]
        ]

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
    watertight = (
        f_count > 0
        and len(boundary_edges) == 0
        and len(non_manifold_edges) == 0
    )

    return TopologyResult(
        variant=variant,
        weld_tolerance=tol,
        weld_tolerance_status=tolerance_status,
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
        degenerate_face_count=len(ingest_result.triangles) - f_count,
    )
