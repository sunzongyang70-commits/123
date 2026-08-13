"""
CLI entry point for the PDOS Local Extractor.

Usage:
    python -m pdos_extractor \\
        --input  "/local/path/model.stl" \\
        --output "./PRIMARY_MESH_EVIDENCE.json" \\
        --topology-mode both \\
        --symmetry-mode off \\
        --fail-on-validation-error
"""

import argparse
import json
import math
import os
import sys

from .stl_ingest import ingest, STLIngestResult
from .topology import compute_topology
from .evidence import build_evidence, build_validation

_DEFAULT_TOPOLOGY_MODE = "both"
_DEFAULT_SYMMETRY_MODE = "off"


def _finite_non_negative_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "weld tolerance must be a finite, non-negative float"
        ) from exc
    if not math.isfinite(value) or value < 0.0:
        raise argparse.ArgumentTypeError(
            "weld tolerance must be a finite, non-negative float"
        )
    return value


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m pdos_extractor",
        description=(
            "Phase 1.2A — Local Ground Truth Evidence Extractor. "
            "Reads a local STL file and writes PRIMARY_MESH_EVIDENCE.json "
            "plus a sibling validation file.  "
            "No external geometry engines required."
        ),
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="Path to the input STL file.",
    )
    parser.add_argument(
        "--output",
        default="PRIMARY_MESH_EVIDENCE.json",
        metavar="PATH",
        help="Destination for PRIMARY_MESH_EVIDENCE.json (default: ./PRIMARY_MESH_EVIDENCE.json).",
    )
    parser.add_argument(
        "--topology-mode",
        choices=["raw", "welded", "both"],
        default=_DEFAULT_TOPOLOGY_MODE,
        help=(
            "Which topology variant(s) to compute. "
            "'raw' = exact coordinate deduplication (weld_tolerance=0.0); "
            "'welded' = deterministic grid-based merge (1e-8 * bbox_diagonal); "
            "'both' = compute and report both (default)."
        ),
    )
    parser.add_argument(
        "--symmetry-mode",
        choices=["off", "optional"],
        default=_DEFAULT_SYMMETRY_MODE,
        help=(
            "Symmetry analysis mode. "
            "'off' = skip entirely (default); "
            "'optional' = attempt, report UNKNOWN if insufficient data."
        ),
    )
    parser.add_argument(
        "--fail-on-validation-error",
        action="store_true",
        default=False,
        help=(
            "Exit with a non-zero code when the validation gate returns FAIL. "
            "PASS_WITH_WARNINGS does NOT cause a non-zero exit."
        ),
    )
    parser.add_argument(
        "--weld-tolerance",
        type=_finite_non_negative_float,
        default=None,
        metavar="FLOAT",
        help=(
            "Override the weld tolerance for the 'welded' variant. "
            "If omitted, derived from 1e-8 * bbox_diagonal."
        ),
    )
    return parser.parse_args(argv)


def _write_json(path: str, obj: dict) -> str:
    """Write obj to path as strict JSON; return the serialized string."""
    s = json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2)
    # Strict round-trip check
    json.loads(s)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(s)
    return s


def main(argv=None) -> int:
    args = _parse_args(argv)

    # ── 1. STL ingest ──────────────────────────────────────────────────────
    if not os.path.isfile(args.input):
        error_doc = {
            "status": "STL_NOT_AVAILABLE",
            "reason": f"Input file not found: {args.input}",
            "input_path": args.input,
        }
        print(json.dumps(error_doc, indent=2), file=sys.stderr)
        return 1

    try:
        ingest_result: STLIngestResult = ingest(args.input)
    except Exception as exc:
        error_doc = {
            "status": "STL_NOT_AVAILABLE",
            "reason": str(exc),
            "input_path": args.input,
        }
        print(json.dumps(error_doc, indent=2), file=sys.stderr)
        return 1

    if not ingest_result.parse_success:
        print(
            json.dumps(
                {
                    "status": "STL_PARSE_ERROR",
                    "reason": ingest_result.parse_error,
                    "input_path": args.input,
                    "sha256": ingest_result.sha256,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        # Continue — we'll surface this in the validation gate.

    # ── 2. Topology ────────────────────────────────────────────────────────
    mode = args.topology_mode
    raw_topo = None
    welded_topo = None

    if ingest_result.parse_success and mode in ("raw", "both"):
        raw_topo = compute_topology(ingest_result, "raw_exact")

    if ingest_result.parse_success and mode in ("welded", "both"):
        welded_topo = compute_topology(
            ingest_result, "welded", weld_tolerance=args.weld_tolerance
        )

    # ── 3. Build evidence doc ──────────────────────────────────────────────
    cli_params = {
        "input": args.input,
        "output": args.output,
        "topology_mode": mode,
        "symmetry_mode": args.symmetry_mode,
        "fail_on_validation_error": args.fail_on_validation_error,
        "weld_tolerance_override": args.weld_tolerance,
    }

    evidence_doc = build_evidence(
        ingest=ingest_result,
        raw=raw_topo,
        welded=welded_topo,
        topology_mode=mode,
        symmetry_mode=args.symmetry_mode,
        cli_parameters=cli_params,
    )

    # Serialize, strict round-trip, write
    output_path = args.output
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    try:
        evidence_json_str = _write_json(output_path, evidence_doc)
    except (ValueError, TypeError) as exc:
        print(f"JSON serialization error: {exc}", file=sys.stderr)
        return 1

    # ── 4. Validation gate ─────────────────────────────────────────────────
    validation_doc = build_validation(
        ingest=ingest_result,
        raw=raw_topo,
        welded=welded_topo,
        evidence_json_str=evidence_json_str,
    )

    # Write sibling validation file
    base, ext = os.path.splitext(output_path)
    validation_path = base + ".validation" + (ext if ext else ".json")
    try:
        _write_json(validation_path, validation_doc)
    except Exception as exc:
        print(f"Could not write validation file: {exc}", file=sys.stderr)
        return 1

    # ── 5. Console summary ────────────────────────────────────────────────
    overall = validation_doc["overall_status"]
    print(f"[pdos_extractor] {overall}")
    print(f"  input  : {args.input}")
    print(f"  output : {output_path}")
    print(f"  validation: {validation_path}")
    if validation_doc.get("warnings"):
        for w in validation_doc["warnings"]:
            print(f"  WARNING: {w}")

    # ── 6. Exit code ──────────────────────────────────────────────────────
    if args.fail_on_validation_error and overall == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
