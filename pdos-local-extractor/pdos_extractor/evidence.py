"""
Evidence Builder — Phase 1.2A
Assembles STLIngestResult + TopologyResult(s) into the PRIMARY_MESH_EVIDENCE
JSON document (strict schema) plus the validation section.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from .stl_ingest import STLIngestResult
from .topology import TopologyResult

SCHEMA = "PDOS_PRIMARY_MESH_EVIDENCE"
SCHEMA_VERSION = "1.0"


# ── provenance helpers ────────────────────────────────────────────────────────

def _prov(
    source_file_hash: str,
    method: str,
    parameters: Dict[str, Any],
    status: str,
) -> Dict[str, Any]:
    return {
        "source": "STL",
        "source_file_hash": source_file_hash,
        "method": method,
        "parameters": parameters,
        "status": status,
    }


# ── topology result → dict ────────────────────────────────────────────────────

def _topology_dict(topo: TopologyResult, sha256: str) -> Dict[str, Any]:
    prov = _prov(
        sha256,
        "deterministic_distance_welding_edge_incidence_and_face_adjacency",
        {"weld_tolerance": topo.weld_tolerance, "variant": topo.variant},
        "DERIVED",
    )
    return {
        "provenance": prov,
        "variant": topo.variant,
        "weld_tolerance": topo.weld_tolerance,
        "weld_tolerance_status": topo.weld_tolerance_status,
        "vertex_count": topo.vertex_count,
        "face_count": topo.face_count,
        "edge_count": topo.edge_count,
        "bounding_box": topo.bounding_box,
        "connected_component_count": topo.connected_component_count,
        "components": topo.components,
        "boundary_edge_count": topo.boundary_edge_count,
        "boundary_loops": topo.boundary_loops,
        "non_manifold_edge_count": topo.non_manifold_edge_count,
        "watertight": topo.watertight,
        "euler_characteristic": topo.euler_characteristic,
        "degenerate_face_count": topo.degenerate_face_count,
        "measurement_cage_used": False,
        "design_prior_used": False,
        "synthetic_geometry_count": 0,
    }


# ── validation gate ───────────────────────────────────────────────────────────

def _sha256_valid(h: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", h))


def _validation_checks(
    ingest: STLIngestResult,
    raw: Optional[TopologyResult],
    welded: Optional[TopologyResult],
    output_json_str: str,
) -> Dict[str, Any]:
    checks = {}
    parsed: Optional[Dict[str, Any]] = None

    def reject_non_standard_constant(value: str) -> None:
        raise ValueError(f"Illegal JSON numeric token: {value}")

    try:
        candidate = json.loads(
            output_json_str,
            parse_constant=reject_non_standard_constant,
        )
        if isinstance(candidate, dict):
            parsed = candidate
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None

    # 1. input file exists
    checks["input_file_exists"] = {
        "result": os.path.isfile(ingest.input_path) if ingest.input_path else False,
        "expected": True,
    }

    # 2. SHA-256 present and valid
    checks["sha256_present_and_valid"] = {
        "result": _sha256_valid(ingest.sha256),
        "expected": True,
    }

    # 3. STL parse success
    checks["stl_parse_success"] = {
        "result": ingest.parse_success,
        "expected": True,
    }

    # 4. triangle count observed (> 0)
    checks["triangle_count_observed"] = {
        "result": ingest.triangle_count > 0,
        "expected": True,
    }

    # 5. output JSON strict parse round-trip
    round_trip_ok = parsed is not None
    checks["output_json_round_trip_success"] = {
        "result": round_trip_ok,
        "expected": True,
    }

    # 6. topology source is STL only
    topology_container = (parsed or {}).get("topology", {})
    topology_docs = (
        [value for value in topology_container.values() if isinstance(value, dict)]
        if isinstance(topology_container, dict) else []
    )
    input_container = (parsed or {}).get("input", {})
    input_prov = (
        input_container.get("provenance", {})
        if isinstance(input_container, dict) else {}
    )
    provenance_records = [input_prov] + [
        topology_doc.get("provenance", {}) for topology_doc in topology_docs
    ]
    expected_variants = {
        name for name, result in (("raw_exact", raw), ("welded", welded))
        if result is not None
    }
    topology_structure_valid = (
        isinstance(topology_container, dict)
        and set(topology_container) == expected_variants
        and len(topology_docs) == len(expected_variants)
    )
    topology_stl_only = topology_structure_valid and all(
        provenance.get("source") == "STL"
        and provenance.get("source_file_hash") == ingest.sha256
        for provenance in provenance_records
    )
    checks["topology_source_is_stl_only"] = {
        "result": topology_stl_only,
        "expected": True,
    }

    # 7. measurement cage used == false
    mc_used = any(
        topology_doc.get("measurement_cage_used") is not False
        for topology_doc in topology_docs
    ) if topology_docs else False
    checks["measurement_cage_used_for_topology"] = {
        "result": mc_used,
        "expected": False,
    }

    # 8. design prior used == false
    design_prior_used = any(
        topology_doc.get("design_prior_used") is not False
        for topology_doc in topology_docs
    ) if topology_docs else False
    checks["design_prior_used_for_topology"] = {
        "result": design_prior_used,
        "expected": False,
    }

    # 9. synthetic geometry count == 0
    synthetic_geometry = (parsed or {}).get("synthetic_geometry", [])
    synthetic_count = len(synthetic_geometry) if isinstance(synthetic_geometry, list) else -1
    for topology_doc in topology_docs:
        value = topology_doc.get("synthetic_geometry_count", -1)
        if not isinstance(value, int) or isinstance(value, bool):
            synthetic_count = -1
            break
        synthetic_count += value
    checks["synthetic_geometry_count"] = {
        "result": synthetic_count,
        "expected": 0,
    }

    # 10. hardcoded feature coordinate count == 0
    integrity = (parsed or {}).get("integrity", {})
    if not isinstance(integrity, dict):
        integrity = {}
    hardcoded_count = integrity.get("hardcoded_feature_coordinate_count", -1)
    if not isinstance(hardcoded_count, int) or isinstance(hardcoded_count, bool):
        hardcoded_count = -1
    checks["hardcoded_feature_coordinate_count"] = {
        "result": hardcoded_count,
        "expected": 0,
    }

    # 11. untraceable primary evidence count == 0
    allowed_provenance_statuses = {"OBSERVED", "DERIVED", "UNKNOWN"}
    required_provenance_keys = {
        "source", "source_file_hash", "method", "parameters", "status"
    }
    untraceable_count = sum(
        1 for provenance in provenance_records
        if not required_provenance_keys.issubset(provenance)
        or provenance.get("status") not in allowed_provenance_statuses
        or provenance.get("source") != "STL"
        or provenance.get("source_file_hash") != ingest.sha256
    )
    checks["untraceable_primary_evidence_count"] = {
        "result": untraceable_count,
        "expected": 0,
    }

    # 12. unknown values explicitly labeled
    symmetry_container = (parsed or {}).get("symmetry", {})
    if not isinstance(symmetry_container, dict):
        symmetry_container = {}
    unknowns_labeled = (
        isinstance(input_container, dict)
        and input_container.get("unit_status") == "UNKNOWN"
        and symmetry_container.get("status") == "UNKNOWN"
    )
    checks["unknown_values_explicitly_labeled"] = {
        "result": unknowns_labeled,
        "expected": True,
    }

    # 13. phase_1_3_decision_count == 0
    phase_decisions = (parsed or {}).get("phase_1_3_decisions", None)
    phase_decision_count = len(phase_decisions) if isinstance(phase_decisions, list) else -1
    checks["phase_1_3_decision_count"] = {
        "result": phase_decision_count,
        "expected": 0,
    }

    # 14. contamination detected == false
    contamination_detected = (
        integrity.get("contamination_detected")
        is not False
        or integrity.get("data_sources") != ["STL"]
    )
    checks["contamination_detected"] = {
        "result": contamination_detected,
        "expected": False,
    }

    # 15. raw/welded topology difference warning
    raw_welded_differ = False
    if raw is not None and welded is not None:
        raw_welded_differ = (
            raw.vertex_count != welded.vertex_count
            or raw.face_count != welded.face_count
            or raw.connected_component_count != welded.connected_component_count
            or raw.boundary_edge_count != welded.boundary_edge_count
        )
    checks["raw_welded_topology_difference_warning"] = {
        "result": raw_welded_differ,
        "note": "True means the two variants differ; a validation warning is issued.",
    }

    # Determine PASS/PASS_WITH_WARNINGS/FAIL
    hard_conditions = [
        "input_file_exists",
        "sha256_present_and_valid",
        "stl_parse_success",
        "triangle_count_observed",
        "output_json_round_trip_success",
        "topology_source_is_stl_only",
        "unknown_values_explicitly_labeled",
    ]

    fail = any(
        not checks[k]["result"] for k in hard_conditions
    )
    # measurement_cage / design_prior must be False
    if checks["measurement_cage_used_for_topology"]["result"] is not False:
        fail = True
    if checks["design_prior_used_for_topology"]["result"] is not False:
        fail = True
    if checks["synthetic_geometry_count"]["result"] != 0:
        fail = True
    if checks["hardcoded_feature_coordinate_count"]["result"] != 0:
        fail = True
    if checks["untraceable_primary_evidence_count"]["result"] != 0:
        fail = True
    if checks["phase_1_3_decision_count"]["result"] != 0:
        fail = True
    if checks["contamination_detected"]["result"] is not False:
        fail = True

    warnings = []
    if raw_welded_differ:
        warnings.append(
            "raw_exact and welded topology variants differ in one or more statistics"
        )
    if welded is not None and welded.weld_tolerance_status == "UNKNOWN":
        warnings.append(
            "weld tolerance is UNKNOWN because the STL bounding-box diagonal is zero"
        )

    if fail:
        overall_status = "FAIL"
    elif warnings:
        overall_status = "PASS_WITH_WARNINGS"
    else:
        overall_status = "PASS"

    return {
        "overall_status": overall_status,
        "warnings": warnings,
        "checks": checks,
    }


# ── main assembly ─────────────────────────────────────────────────────────────

def build_evidence(
    ingest: STLIngestResult,
    raw: Optional[TopologyResult],
    welded: Optional[TopologyResult],
    topology_mode: str,
    symmetry_mode: str,
    cli_parameters: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build the PRIMARY_MESH_EVIDENCE dict.
    JSON serialization and round-trip validation happen in the caller.
    """
    sha = ingest.sha256
    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "phase": "1.2A",
        "cli_parameters": cli_parameters,
        "input": {
            "provenance": _prov(
                sha,
                "os_stat_and_sha256",
                {"algorithm": "SHA-256"},
                "OBSERVED",
            ),
            "filename": ingest.filename,
            "input_path": ingest.input_path,
            "byte_size": ingest.byte_size,
            "sha256": sha,
            "detected_format": ingest.detected_format,
            "unit_status": ingest.unit_status,
            "triangle_count": ingest.triangle_count,
            "parse_success": ingest.parse_success,
            "parse_error": ingest.parse_error if ingest.parse_error else None,
        },
        "topology_mode": topology_mode,
        "topology": {},
        "symmetry": {
            "mode": symmetry_mode,
            "status": "UNKNOWN",
            "reason": (
                "Symmetry analysis not implemented in v0.1. "
                "Requires nearest-point-to-triangle-surface computation."
            ),
        },
        "phase_1_3_decisions": [],
        "synthetic_geometry": [],
        "integrity": {
            "data_sources": ["STL"],
            "synthetic_geometry_count": 0,
            "hardcoded_feature_coordinate_count": 0,
            "phase_1_3_decision_count": 0,
            "contamination_detected": False,
        },
    }

    if raw is not None:
        doc["topology"]["raw_exact"] = _topology_dict(raw, sha)
    if welded is not None:
        doc["topology"]["welded"] = _topology_dict(welded, sha)

    return doc


def build_validation(
    ingest: STLIngestResult,
    raw: Optional[TopologyResult],
    welded: Optional[TopologyResult],
    evidence_json_str: str,
) -> Dict[str, Any]:
    """Build the validation JSON document."""
    gate = _validation_checks(ingest, raw, welded, evidence_json_str)
    return {
        "schema": "PDOS_PRIMARY_MESH_EVIDENCE_VALIDATION",
        "schema_version": "1.0",
        "source_sha256": ingest.sha256,
        **gate,
    }
