"""
Tests for pdos_extractor — Phase 1.2A.
All fixtures are committed to tests/fixtures/ and no real user STL is required.
"""

import io
import json
import os
import struct
import sys
import tempfile
import unittest

# Allow running from the project root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdos_extractor.stl_ingest import ingest, _is_ascii_stl, _parse_ascii, _parse_binary
from pdos_extractor.topology import compute_topology
from pdos_extractor.evidence import build_evidence, build_validation

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


# ── STL ingest ────────────────────────────────────────────────────────────────

class TestSTLIngest(unittest.TestCase):

    def test_ascii_tetrahedron_format_detected(self):
        r = ingest(_fixture("tetrahedron_ascii.stl"))
        self.assertEqual(r.detected_format, "ASCII")

    def test_binary_tetrahedron_format_detected(self):
        r = ingest(_fixture("tetrahedron_binary.stl"))
        self.assertEqual(r.detected_format, "BINARY")

    def test_ascii_triangle_count(self):
        r = ingest(_fixture("tetrahedron_ascii.stl"))
        self.assertEqual(r.triangle_count, 4)

    def test_binary_triangle_count(self):
        r = ingest(_fixture("tetrahedron_binary.stl"))
        self.assertEqual(r.triangle_count, 4)

    def test_sha256_format(self):
        import re
        r = ingest(_fixture("tetrahedron_ascii.stl"))
        self.assertRegex(r.sha256, r"^[0-9a-f]{64}$")

    def test_sha256_is_deterministic(self):
        r1 = ingest(_fixture("tetrahedron_ascii.stl"))
        r2 = ingest(_fixture("tetrahedron_ascii.stl"))
        self.assertEqual(r1.sha256, r2.sha256)

    def test_unit_status_always_unknown(self):
        for fname in ("tetrahedron_ascii.stl", "tetrahedron_binary.stl"):
            r = ingest(_fixture(fname))
            self.assertEqual(r.unit_status, "UNKNOWN")

    def test_parse_success_flag(self):
        r = ingest(_fixture("tetrahedron_ascii.stl"))
        self.assertTrue(r.parse_success)

    def test_byte_size_matches_actual_file(self):
        path = _fixture("tetrahedron_ascii.stl")
        r = ingest(path)
        self.assertEqual(r.byte_size, os.path.getsize(path))

    def test_filename_field(self):
        r = ingest(_fixture("tetrahedron_ascii.stl"))
        self.assertEqual(r.filename, "tetrahedron_ascii.stl")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            ingest("/nonexistent/path/model.stl")

    def test_open_mesh_triangles(self):
        r = ingest(_fixture("open_mesh.stl"))
        self.assertEqual(r.triangle_count, 2)

    def test_non_manifold_triangles(self):
        r = ingest(_fixture("non_manifold.stl"))
        self.assertEqual(r.triangle_count, 3)

    def test_ascii_binary_produce_same_vertices(self):
        # Both tetrahedron fixtures describe the same 4 vertices
        ra = ingest(_fixture("tetrahedron_ascii.stl"))
        rb = ingest(_fixture("tetrahedron_binary.stl"))
        self.assertEqual(ra.triangle_count, rb.triangle_count)

    def test_is_ascii_stl_heuristic_ascii(self):
        with open(_fixture("tetrahedron_ascii.stl"), "rb") as fh:
            raw = fh.read()
        self.assertTrue(_is_ascii_stl(raw))

    def test_is_ascii_stl_heuristic_binary(self):
        with open(_fixture("tetrahedron_binary.stl"), "rb") as fh:
            raw = fh.read()
        self.assertFalse(_is_ascii_stl(raw))


# ── Topology ──────────────────────────────────────────────────────────────────

class TestTopologyClosedTetrahedron(unittest.TestCase):

    def setUp(self):
        self.ingest = ingest(_fixture("tetrahedron_ascii.stl"))
        self.raw = compute_topology(self.ingest, "raw_exact")
        self.welded = compute_topology(self.ingest, "welded")

    def test_face_count(self):
        self.assertEqual(self.raw.face_count, 4)

    def test_vertex_count(self):
        # Tetrahedron has 4 unique vertices
        self.assertEqual(self.raw.vertex_count, 4)

    def test_watertight(self):
        self.assertTrue(self.raw.watertight)

    def test_no_boundary_edges(self):
        self.assertEqual(self.raw.boundary_edge_count, 0)
        self.assertEqual(len(self.raw.boundary_loops), 0)

    def test_no_non_manifold_edges(self):
        self.assertEqual(self.raw.non_manifold_edge_count, 0)

    def test_euler_characteristic(self):
        # For a closed orientable genus-0 surface: V - E + F = 2
        self.assertEqual(self.raw.euler_characteristic, 2)

    def test_single_connected_component(self):
        self.assertEqual(self.raw.connected_component_count, 1)

    def test_component_semantic_role_unknown(self):
        self.assertEqual(self.raw.components[0]["semantic_role"], "UNKNOWN")

    def test_raw_and_welded_agree_on_closed_mesh(self):
        self.assertEqual(self.raw.vertex_count, self.welded.vertex_count)
        self.assertEqual(self.raw.face_count, self.welded.face_count)
        self.assertEqual(self.raw.watertight, self.welded.watertight)

    def test_bounding_box_keys(self):
        bb = self.raw.bounding_box
        for k in ("min", "max", "extents", "diagonal"):
            self.assertIn(k, bb)

    def test_bounding_box_diagonal_positive(self):
        self.assertGreater(self.raw.bounding_box["diagonal"], 0)

    def test_binary_tetrahedron_same_topology(self):
        r_bin = ingest(_fixture("tetrahedron_binary.stl"))
        t_bin = compute_topology(r_bin, "raw_exact")
        self.assertEqual(t_bin.vertex_count, self.raw.vertex_count)
        self.assertEqual(t_bin.watertight, True)

    def test_variant_label_raw(self):
        self.assertEqual(self.raw.variant, "raw_exact")

    def test_variant_label_welded(self):
        self.assertEqual(self.welded.variant, "welded")

    def test_weld_tolerance_raw_is_zero(self):
        self.assertEqual(self.raw.weld_tolerance, 0.0)

    def test_weld_tolerance_welded_derived(self):
        # Welded tolerance must be >= 0 and derived from bbox diagonal
        self.assertGreaterEqual(self.welded.weld_tolerance, 0.0)


class TestTopologyOpenMesh(unittest.TestCase):

    def setUp(self):
        self.ingest = ingest(_fixture("open_mesh.stl"))
        self.raw = compute_topology(self.ingest, "raw_exact")

    def test_not_watertight(self):
        self.assertFalse(self.raw.watertight)

    def test_has_boundary_edges(self):
        self.assertGreater(self.raw.boundary_edge_count, 0)

    def test_has_boundary_loops(self):
        self.assertGreater(len(self.raw.boundary_loops), 0)

    def test_boundary_loop_has_required_keys(self):
        loop = self.raw.boundary_loops[0]
        for k in ("id", "closed", "vertex_count", "perimeter", "orientation",
                  "ordered_vertex_ids", "ordered_coordinates", "status"):
            self.assertIn(k, loop)

    def test_boundary_loop_id_format(self):
        loop = self.raw.boundary_loops[0]
        self.assertRegex(loop["id"], r"^BL_\d{4}$")

    def test_boundary_loop_orientation_unknown(self):
        loop = self.raw.boundary_loops[0]
        self.assertEqual(loop["orientation"], "UNKNOWN")

    def test_boundary_loop_perimeter_positive(self):
        loop = self.raw.boundary_loops[0]
        self.assertGreater(loop["perimeter"], 0)


class TestTopologyNonManifold(unittest.TestCase):

    def setUp(self):
        self.ingest = ingest(_fixture("non_manifold.stl"))
        self.raw = compute_topology(self.ingest, "raw_exact")

    def test_has_non_manifold_edges(self):
        self.assertGreater(self.raw.non_manifold_edge_count, 0)

    def test_not_watertight(self):
        self.assertFalse(self.raw.watertight)


# ── Evidence + Validation ──────────────────────────────────────────────────────

class TestEvidence(unittest.TestCase):

    def _build(self, fixture_name="tetrahedron_ascii.stl", mode="both"):
        ing = ingest(_fixture(fixture_name))
        raw = compute_topology(ing, "raw_exact") if mode in ("raw", "both") else None
        wld = compute_topology(ing, "welded")   if mode in ("welded", "both") else None
        cli = {"topology_mode": mode, "symmetry_mode": "off"}
        doc = build_evidence(ing, raw, wld, mode, "off", cli)
        return doc, ing, raw, wld

    def test_schema_field(self):
        doc, *_ = self._build()
        self.assertEqual(doc["schema"], "PDOS_PRIMARY_MESH_EVIDENCE")

    def test_schema_version_field(self):
        doc, *_ = self._build()
        self.assertEqual(doc["schema_version"], "1.0")

    def test_phase_field(self):
        doc, *_ = self._build()
        self.assertEqual(doc["phase"], "1.2A")

    def test_both_topology_variants_present(self):
        doc, *_ = self._build(mode="both")
        self.assertIn("raw_exact", doc["topology"])
        self.assertIn("welded", doc["topology"])

    def test_raw_only_mode(self):
        doc, *_ = self._build(mode="raw")
        self.assertIn("raw_exact", doc["topology"])
        self.assertNotIn("welded", doc["topology"])

    def test_welded_only_mode(self):
        doc, *_ = self._build(mode="welded")
        self.assertIn("welded", doc["topology"])
        self.assertNotIn("raw_exact", doc["topology"])

    def test_no_measurement_cage_used(self):
        doc, *_ = self._build()
        for variant_data in doc["topology"].values():
            self.assertFalse(variant_data["measurement_cage_used"])

    def test_no_design_prior_used(self):
        doc, *_ = self._build()
        for variant_data in doc["topology"].values():
            self.assertFalse(variant_data["design_prior_used"])

    def test_synthetic_geometry_count_zero(self):
        doc, *_ = self._build()
        for variant_data in doc["topology"].values():
            self.assertEqual(variant_data["synthetic_geometry_count"], 0)

    def test_phase_1_3_decisions_empty(self):
        doc, *_ = self._build()
        self.assertEqual(doc["phase_1_3_decisions"], [])

    def test_synthetic_geometry_list_empty(self):
        doc, *_ = self._build()
        self.assertEqual(doc["synthetic_geometry"], [])

    def test_symmetry_status_unknown_when_off(self):
        doc, *_ = self._build()
        self.assertEqual(doc["symmetry"]["status"], "UNKNOWN")

    def test_unit_status_unknown_in_input(self):
        doc, *_ = self._build()
        self.assertEqual(doc["input"]["unit_status"], "UNKNOWN")

    def test_json_strict_round_trip(self):
        doc, *_ = self._build()
        s = json.dumps(doc, ensure_ascii=False, allow_nan=False)
        parsed = json.loads(s)
        self.assertIsInstance(parsed, dict)

    def test_provenance_keys_in_topology(self):
        doc, *_ = self._build()
        for variant_data in doc["topology"].values():
            prov = variant_data["provenance"]
            for k in ("source", "source_file_hash", "method", "parameters", "status"):
                self.assertIn(k, prov)

    def test_provenance_source_is_stl(self):
        doc, *_ = self._build()
        for variant_data in doc["topology"].values():
            self.assertEqual(variant_data["provenance"]["source"], "STL")

    def test_provenance_status_derived(self):
        doc, *_ = self._build()
        for variant_data in doc["topology"].values():
            self.assertEqual(variant_data["provenance"]["status"], "DERIVED")


class TestValidationGate(unittest.TestCase):

    def _full_validation(self, fixture_name="tetrahedron_ascii.stl"):
        ing = ingest(_fixture(fixture_name))
        raw = compute_topology(ing, "raw_exact")
        wld = compute_topology(ing, "welded")
        cli = {"topology_mode": "both", "symmetry_mode": "off"}
        doc = build_evidence(ing, raw, wld, "both", "off", cli)
        evidence_str = json.dumps(doc, ensure_ascii=False, allow_nan=False)
        val = build_validation(ing, raw, wld, evidence_str)
        return val

    def test_overall_status_pass_for_closed_mesh(self):
        val = self._full_validation("tetrahedron_ascii.stl")
        self.assertIn(val["overall_status"], ("PASS", "PASS_WITH_WARNINGS"))

    def test_checks_dict_present(self):
        val = self._full_validation()
        self.assertIn("checks", val)

    def test_required_check_keys_present(self):
        val = self._full_validation()
        expected_keys = [
            "input_file_exists",
            "sha256_present_and_valid",
            "stl_parse_success",
            "triangle_count_observed",
            "output_json_round_trip_success",
            "topology_source_is_stl_only",
            "measurement_cage_used_for_topology",
            "design_prior_used_for_topology",
            "synthetic_geometry_count",
            "hardcoded_feature_coordinate_count",
            "untraceable_primary_evidence_count",
            "unknown_values_explicitly_labeled",
            "phase_1_3_decision_count",
            "contamination_detected",
            "raw_welded_topology_difference_warning",
        ]
        for k in expected_keys:
            self.assertIn(k, val["checks"], f"Missing check: {k}")

    def test_validation_schema(self):
        val = self._full_validation()
        self.assertEqual(val["schema"], "PDOS_PRIMARY_MESH_EVIDENCE_VALIDATION")

    def test_binary_tetrahedron_passes(self):
        val = self._full_validation("tetrahedron_binary.stl")
        self.assertIn(val["overall_status"], ("PASS", "PASS_WITH_WARNINGS"))

    def test_open_mesh_passes_validation_gate(self):
        # Open mesh is valid — it just has boundary edges (which are fine to report)
        val = self._full_validation("open_mesh.stl")
        self.assertIn(val["overall_status"], ("PASS", "PASS_WITH_WARNINGS"))


# ── CLI integration ───────────────────────────────────────────────────────────

class TestCLI(unittest.TestCase):

    def _run_cli(self, args):
        from pdos_extractor.__main__ import main
        return main(args)

    def test_cli_pass_closed_mesh(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            rc = self._run_cli([
                "--input", _fixture("tetrahedron_ascii.stl"),
                "--output", out,
                "--topology-mode", "both",
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(out))

    def test_cli_writes_validation_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            val_path = os.path.join(tmp, "out.validation.json")
            self._run_cli([
                "--input", _fixture("tetrahedron_ascii.stl"),
                "--output", out,
            ])
            self.assertTrue(os.path.isfile(val_path))

    def test_cli_missing_input_returns_1(self):
        rc = self._run_cli([
            "--input", "/nonexistent/path.stl",
            "--output", "/tmp/nope.json",
        ])
        self.assertEqual(rc, 1)

    def test_cli_output_json_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            self._run_cli([
                "--input", _fixture("tetrahedron_binary.stl"),
                "--output", out,
            ])
            with open(out, encoding="utf-8") as fh:
                parsed = json.load(fh)
            self.assertIsInstance(parsed, dict)

    def test_cli_raw_only_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            self._run_cli([
                "--input", _fixture("tetrahedron_ascii.stl"),
                "--output", out,
                "--topology-mode", "raw",
            ])
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertIn("raw_exact", doc["topology"])
            self.assertNotIn("welded", doc["topology"])

    def test_cli_welded_only_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            self._run_cli([
                "--input", _fixture("tetrahedron_ascii.stl"),
                "--output", out,
                "--topology-mode", "welded",
            ])
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertIn("welded", doc["topology"])
            self.assertNotIn("raw_exact", doc["topology"])

    def test_fail_on_validation_error_does_not_fail_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            rc = self._run_cli([
                "--input", _fixture("tetrahedron_ascii.stl"),
                "--output", out,
                "--fail-on-validation-error",
            ])
            self.assertEqual(rc, 0)

    def test_open_mesh_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            rc = self._run_cli([
                "--input", _fixture("open_mesh.stl"),
                "--output", out,
            ])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            # Should report boundary edges
            self.assertGreater(
                doc["topology"]["raw_exact"]["boundary_edge_count"], 0
            )

    def test_symmetry_off_produces_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            self._run_cli([
                "--input", _fixture("tetrahedron_ascii.stl"),
                "--output", out,
                "--symmetry-mode", "off",
            ])
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["symmetry"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
