"""Executable smoke test for the local-only macOS helper."""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "run_extractor_macos.command")
FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "tetrahedron_ascii.stl"
)


class TestMacOSHelper(unittest.TestCase):
    def test_script_is_executable(self):
        mode = os.stat(SCRIPT).st_mode
        self.assertTrue(mode & stat.S_IXUSR)

    def test_helper_handles_chinese_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = os.path.join(directory, "休息舱 测试.StL")
            shutil.copyfile(FIXTURE, input_path)
            environment = dict(os.environ)
            environment.update({
                "PDOS_NO_PAUSE": "1",
                "PDOS_STL_PATH": input_path,
            })
            completed = subprocess.run(
                [SCRIPT],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            evidence_path = os.path.join(directory, "PRIMARY_MESH_EVIDENCE.json")
            validation_path = os.path.join(
                directory, "PRIMARY_MESH_EVIDENCE.validation.json"
            )
            self.assertTrue(os.path.isfile(evidence_path))
            with open(evidence_path, encoding="utf-8") as handle:
                self.assertEqual(
                    json.load(handle)["schema"], "PDOS_PRIMARY_MESH_EVIDENCE"
                )
            with open(validation_path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["overall_status"], "PASS")


if __name__ == "__main__":
    unittest.main()
