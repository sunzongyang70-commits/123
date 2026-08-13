"""Regression tests for the Phase 1.2A hardening blockers."""

import contextlib
import io
import json
import math
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdos_extractor.__main__ import main
from pdos_extractor.evidence import build_evidence, build_validation
from pdos_extractor.stl_ingest import (
    STLIngestResult,
    _is_ascii_stl,
    _parse_ascii,
    ingest,
)
from pdos_extractor.topology import (
    _deduplicate_vertices,
    _extract_boundary_loops,
    compute_topology,
)


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    return os.path.join(FIXTURES, name)


def triangle(a, b, c):
    return ((0.0, 0.0, 1.0), a, b, c)


def ingest_from_triangles(triangles):
    return STLIngestResult(
        input_path=fixture("tetrahedron_ascii.stl"),
        filename="synthetic-test-fixture.stl",
        byte_size=1,
        sha256="a" * 64,
        detected_format="ASCII",
        unit_status="UNKNOWN",
        triangle_count=len(triangles),
        triangles=triangles,
        parse_success=True,
        parse_error=None,
    )


def ascii_stl(facet_body, normal="0 0 1"):
    return (
        "solid test\n"
        f"facet normal {normal}\n"
        "outer loop\n"
        f"{facet_body}\n"
        "endloop\n"
        "endfacet\n"
        "endsolid test\n"
    ).encode("utf-8")


class TestStrictASCIIParser(unittest.TestCase):
    def assert_parse_error(self, raw, reason_fragment):
        triangles, error = _parse_ascii(raw)
        self.assertIsNotNone(error)
        self.assertIn(reason_fragment, json.dumps(error))
        self.assertFalse(any(
            vertex == (0.0, 0.0, 0.0)
            for tri in triangles for vertex in tri[1:]
        ))

    def test_malformed_vertex_text_is_not_replaced_with_origin(self):
        self.assert_parse_error(
            ascii_stl("vertex x y z\nvertex 1 0 0\nvertex 0 1 0"),
            "Malformed vertex coordinate",
        )

    def test_vertex_with_too_few_coordinates_fails(self):
        self.assert_parse_error(
            ascii_stl("vertex 1 2\nvertex 1 0 0\nvertex 0 1 0"),
            "expected 3 coordinates",
        )

    def test_facet_with_only_two_vertices_fails(self):
        self.assert_parse_error(
            ascii_stl("vertex 1 0 0\nvertex 0 1 0"),
            "exactly 3 valid vertices",
        )

    def test_non_finite_vertex_fails(self):
        self.assert_parse_error(
            ascii_stl("vertex NaN 0 0\nvertex 1 0 0\nvertex 0 1 0"),
            "Non-finite vertex coordinate",
        )

    def test_malformed_normal_fails(self):
        self.assert_parse_error(
            ascii_stl("vertex 0 0 1\nvertex 1 0 0\nvertex 0 1 0", "0 bad 1"),
            "Malformed facet normal coordinate",
        )

    def test_structured_error_has_line_reason_and_raw_line(self):
        _, error = _parse_ascii(
            ascii_stl("vertex bad 0 0\nvertex 1 0 0\nvertex 0 1 0")
        )
        self.assertIsInstance(error, dict)
        for key in ("line_number", "reason", "raw_line", "error_count"):
            self.assertIn(key, error)

    def test_binary_header_starting_with_solid_stays_binary(self):
        header = bytearray(80)
        header[:18] = b"solid facet normal"
        record = struct.pack(
            "<12fH",
            0, 0, 1,
            0, 0, 0,
            1, 0, 0,
            0, 1, 0,
            0,
        )
        raw = bytes(header) + struct.pack("<I", 1) + record
        self.assertFalse(_is_ascii_stl(raw))
        with tempfile.NamedTemporaryFile(suffix=".stl") as handle:
            handle.write(raw)
            handle.flush()
            self.assertEqual(ingest(handle.name).detected_format, "BINARY")

    def test_truncated_binary_is_parse_error(self):
        raw = bytes(80) + struct.pack("<I", 1) + bytes(10)
        with tempfile.NamedTemporaryFile(suffix=".stl") as handle:
            handle.write(raw)
            handle.flush()
            result = ingest(handle.name)
        self.assertFalse(result.parse_success)
        self.assertIn("Truncated binary STL", result.parse_error["reason"])

    def test_chinese_filename_path_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "休息舱 模型.stl")
            with open(fixture("tetrahedron_ascii.stl"), "rb") as source:
                with open(path, "wb") as destination:
                    destination.write(source.read())
            result = ingest(path)
            self.assertTrue(result.parse_success)
            self.assertEqual(result.filename, "休息舱 模型.stl")


class TestDistanceBasedWelding(unittest.TestCase):
    def test_same_bucket_but_farther_than_tolerance_does_not_weld(self):
        triangles = [
            triangle((0.1, 0.1, 0.1), (0.9, 0.9, 0.9), (3.0, 0.0, 0.0))
        ]
        vertices, faces = _deduplicate_vertices(triangles, 1.0)
        self.assertEqual(len(vertices), 3)
        self.assertEqual(len(faces), 1)

    def test_adjacent_buckets_within_tolerance_weld(self):
        triangles = [
            triangle((0.99, 0, 0), (0, 2, 0), (0, 0, 2)),
            triangle((1.01, 0, 0), (0, 4, 0), (0, 0, 4)),
        ]
        vertices, faces = _deduplicate_vertices(triangles, 0.05)
        self.assertEqual(faces[0][0], faces[1][0])

    def test_exactly_tolerance_welds(self):
        triangles = [
            triangle((0, 0, 0), (0, 2, 0), (0, 0, 2)),
            triangle((1, 0, 0), (0, 4, 0), (0, 0, 4)),
        ]
        _, faces = _deduplicate_vertices(triangles, 1.0)
        self.assertEqual(faces[0][0], faces[1][0])

    def test_just_over_tolerance_does_not_weld(self):
        triangles = [
            triangle((0, 0, 0), (0, 2, 0), (0, 0, 2)),
            triangle((math.nextafter(1.0, math.inf), 0, 0), (0, 4, 0), (0, 0, 4)),
        ]
        _, faces = _deduplicate_vertices(triangles, 1.0)
        self.assertNotEqual(faces[0][0], faces[1][0])

    def test_distance_tie_uses_lower_canonical_vertex_id(self):
        triangles = [
            triangle((-1, 0, 0), (1, 0, 0), (0, 5, 0)),
            triangle((0, 0, 0), (0, 10, 0), (0, 0, 10)),
        ]
        _, faces = _deduplicate_vertices(triangles, 1.1)
        self.assertEqual(faces[1][0], faces[0][0])

    def test_invalid_tolerances_are_rejected(self):
        triangles = [triangle((0, 0, 0), (1, 0, 0), (0, 1, 0))]
        for value in (-1.0, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite, non-negative"):
                    _deduplicate_vertices(triangles, value)

    def test_zero_tolerance_uses_raw_exact_without_division(self):
        triangles = [triangle((0, 0, 0), (1, 0, 0), (0, 1, 0))]
        vertices, faces = _deduplicate_vertices(triangles, 0.0)
        self.assertEqual(len(vertices), 3)
        self.assertEqual(len(faces), 1)

    def test_subnormal_positive_tolerance_does_not_overflow_grid_index(self):
        triangles = [triangle((1, 0, 0), (2, 0, 0), (1, 1, 0))]
        vertices, faces = _deduplicate_vertices(triangles, float.fromhex("0x0.0000000000001p-1022"))
        self.assertEqual(len(vertices), 3)
        self.assertEqual(len(faces), 1)

    def test_zero_diagonal_reports_unknown_derived_tolerance(self):
        result = compute_topology(
            ingest_from_triangles([
                triangle((2, 2, 2), (2, 2, 2), (2, 2, 2))
            ]),
            "welded",
        )
        self.assertEqual(result.weld_tolerance, 0.0)
        self.assertEqual(result.weld_tolerance_status, "UNKNOWN")
        self.assertEqual(result.degenerate_face_count, 1)

    def test_repeated_runs_have_identical_vertices_and_faces(self):
        triangles = [
            triangle((0.99, 0, 0), (0, 2, 0), (0, 0, 2)),
            triangle((1.01, 0, 0), (0, 4, 0), (0, 0, 4)),
        ]
        self.assertEqual(
            _deduplicate_vertices(triangles, 0.05),
            _deduplicate_vertices(triangles, 0.05),
        )


class TestBoundaryGraphClassification(unittest.TestCase):
    vertices = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (2.0, 0.0, 0.0),
        (3.0, 0.0, 0.0),
        (3.0, 1.0, 0.0),
    ]

    def test_single_triangle_is_explicit_closed_loop(self):
        records = _extract_boundary_loops([(0, 1), (1, 2), (0, 2)], self.vertices)
        record = records[0]
        self.assertTrue(record["closed"])
        self.assertEqual(record["status"], "CLOSED_LOOP")
        self.assertEqual(record["ordered_vertex_ids"][0], record["ordered_vertex_ids"][-1])
        self.assertEqual(record["ordered_coordinates"][0], record["ordered_coordinates"][-1])

    def test_two_triangles_square_has_closed_outer_boundary(self):
        ingest_result = ingest_from_triangles([
            triangle(self.vertices[0], self.vertices[1], self.vertices[2]),
            triangle(self.vertices[0], self.vertices[2], self.vertices[3]),
        ])
        record = compute_topology(ingest_result, "raw_exact").boundary_loops[0]
        self.assertTrue(record["closed"])
        self.assertAlmostEqual(record["perimeter"], 4.0)

    def test_open_chain_starts_at_smaller_degree_one_vertex(self):
        record = _extract_boundary_loops([(0, 1), (1, 2)], self.vertices)[0]
        self.assertFalse(record["closed"])
        self.assertEqual(record["status"], "OPEN_CHAIN")
        self.assertEqual(record["ordered_vertex_ids"], [0, 1, 2])

    def test_branch_is_graph_level_not_invented_traversal(self):
        record = _extract_boundary_loops(
            [(0, 1), (1, 2), (1, 3)], self.vertices
        )[0]
        self.assertEqual(record["status"], "BRANCHED_BOUNDARY_GRAPH")
        self.assertFalse(record["closed"])
        self.assertEqual(record["ordered_vertex_ids"], [])
        self.assertEqual(len(record["graph_edges"]), 3)
        self.assertEqual(record["vertex_degrees"]["1"], 3)

    def test_multiple_loops_have_stable_ids_and_order(self):
        edges = [
            (4, 5), (5, 6), (4, 6),
            (0, 1), (1, 2), (2, 3), (0, 3),
        ]
        first = _extract_boundary_loops(edges, self.vertices)
        second = _extract_boundary_loops(list(reversed(edges)), self.vertices)
        self.assertEqual(first, second)
        self.assertEqual([record["id"] for record in first], ["BL_0001", "BL_0002"])
        self.assertEqual(min(first[0]["graph_vertex_ids"]), 0)

    def test_boundary_coordinates_are_only_source_vertices(self):
        record = _extract_boundary_loops(
            [(0, 1), (1, 2), (2, 3), (0, 3)], self.vertices
        )[0]
        source_coordinates = {tuple(value) for value in self.vertices}
        self.assertTrue(all(
            tuple(value) in source_coordinates
            for value in record["ordered_coordinates"]
        ))


class TestTopologyConnectivityHardening(unittest.TestCase):
    def test_multiple_disconnected_components(self):
        result = compute_topology(
            ingest_from_triangles([
                triangle((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                triangle((10, 0, 0), (11, 0, 0), (10, 1, 0)),
            ]),
            "raw_exact",
        )
        self.assertEqual(result.connected_component_count, 2)

    def test_faces_on_non_manifold_edge_are_one_component(self):
        result = compute_topology(
            ingest_from_triangles([
                triangle((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                triangle((0, 0, 0), (1, 0, 0), (0, -1, 0)),
                triangle((0, 0, 0), (1, 0, 0), (0, 0, 1)),
            ]),
            "raw_exact",
        )
        self.assertEqual(result.non_manifold_edge_count, 1)
        self.assertEqual(result.connected_component_count, 1)


class TestValidationHardening(unittest.TestCase):
    def setUp(self):
        self.ingest = ingest(fixture("tetrahedron_ascii.stl"))
        self.raw = compute_topology(self.ingest, "raw_exact")
        self.welded = compute_topology(self.ingest, "welded")

    def document(self):
        return build_evidence(
            self.ingest,
            self.raw,
            self.welded,
            "both",
            "off",
            {"topology_mode": "both", "symmetry_mode": "off"},
        )

    def validate(self, document):
        return build_validation(
            self.ingest,
            self.raw,
            self.welded,
            json.dumps(document, allow_nan=False),
        )

    def test_gate_fails_if_synthetic_geometry_is_present(self):
        document = self.document()
        document["synthetic_geometry"].append([1, 2, 3])
        validation = self.validate(document)
        self.assertEqual(validation["overall_status"], "FAIL")
        self.assertEqual(validation["checks"]["synthetic_geometry_count"]["result"], 1)

    def test_gate_fails_if_provenance_hash_is_tampered(self):
        document = self.document()
        document["topology"]["raw_exact"]["provenance"]["source_file_hash"] = "0" * 64
        validation = self.validate(document)
        self.assertEqual(validation["overall_status"], "FAIL")
        self.assertFalse(validation["checks"]["topology_source_is_stl_only"]["result"])

    def test_gate_rejects_non_standard_nan_token(self):
        validation = build_validation(
            self.ingest, self.raw, self.welded, '{"bad": NaN}'
        )
        self.assertEqual(validation["overall_status"], "FAIL")
        self.assertFalse(validation["checks"]["output_json_round_trip_success"]["result"])

    def test_zero_diagonal_welded_is_pass_with_warnings(self):
        zero_ingest = ingest_from_triangles([
            triangle((2, 2, 2), (2, 2, 2), (2, 2, 2))
        ])
        raw = compute_topology(zero_ingest, "raw_exact")
        welded = compute_topology(zero_ingest, "welded")
        document = build_evidence(
            zero_ingest, raw, welded, "both", "off", {"topology_mode": "both"}
        )
        validation = build_validation(
            zero_ingest, raw, welded, json.dumps(document, allow_nan=False)
        )
        self.assertEqual(validation["overall_status"], "PASS_WITH_WARNINGS")

    def test_evidence_has_no_timestamp_and_is_repeatable(self):
        first = self.document()
        second = self.document()
        self.assertNotIn("generated_utc", first)
        self.assertEqual(first, second)

    def test_raw_welded_difference_is_pass_with_warnings(self):
        near = ingest_from_triangles([
            triangle((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            triangle((0.0000001, 0, 0), (1.0000001, 0, 0), (1, 1, 0)),
        ])
        raw = compute_topology(near, "raw_exact")
        welded = compute_topology(near, "welded", weld_tolerance=0.000001)
        document = build_evidence(
            near, raw, welded, "both", "off", {"topology_mode": "both"}
        )
        validation = build_validation(
            near, raw, welded, json.dumps(document, allow_nan=False)
        )
        self.assertEqual(validation["overall_status"], "PASS_WITH_WARNINGS")
        self.assertTrue(
            validation["checks"]["raw_welded_topology_difference_warning"]["result"]
        )


class TestCLIHardening(unittest.TestCase):
    def test_malformed_ascii_writes_fail_validation_and_no_topology(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = os.path.join(directory, "bad.stl")
            output_path = os.path.join(directory, "PRIMARY_MESH_EVIDENCE.json")
            with open(input_path, "wb") as handle:
                handle.write(ascii_stl(
                    "vertex bad 0 0\nvertex 1 0 0\nvertex 0 1 0"
                ))
            with contextlib.redirect_stderr(io.StringIO()):
                return_code = main([
                    "--input", input_path,
                    "--output", output_path,
                    "--fail-on-validation-error",
                ])
            self.assertEqual(return_code, 1)
            with open(output_path, encoding="utf-8") as handle:
                evidence = json.load(handle)
            with open(
                os.path.join(directory, "PRIMARY_MESH_EVIDENCE.validation.json"),
                encoding="utf-8",
            ) as handle:
                validation = json.load(handle)
            self.assertFalse(evidence["input"]["parse_success"])
            self.assertEqual(evidence["topology"], {})
            self.assertEqual(validation["overall_status"], "FAIL")

    def test_cli_rejects_negative_nan_and_infinity_tolerance(self):
        for value in ("-1", "NaN", "Infinity"):
            with self.subTest(value=value):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main([
                            "--input", fixture("tetrahedron_ascii.stl"),
                            "--weld-tolerance", value,
                        ])
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
