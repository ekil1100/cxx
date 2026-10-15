#!/usr/bin/env python3
"""Focused negative checks for the prototype's scan contract and tool selection."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("p04_runner", ROOT / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerContract(unittest.TestCase):
    def setUp(self):
        # Use captured real scanner output, with its recorded absolute paths.
        report = json.loads((ROOT / "evidence/llvm20.json").read_text())
        command = next(item for item in report["commands"]
                       if "-format=p1689" in item["argv"] and item["returncode"] == 0)
        self.directory = Path(command["cwd"])
        self.data = json.loads(command["stdout"])

    def test_real_scan(self):
        runner.validate_scan(self.data, self.directory)

    def test_reject_bad_scan(self):
        for mutation in ("version", "missing-rule", "duplicate-rule", "wrong-import",
                         "wrong-provider", "non-interface", "wrong-source", "wrong-output"):
            with self.subTest(mutation=mutation):
                data = copy.deepcopy(self.data)
                provider = next(rule for rule in data["rules"] if rule.get("provides"))
                importer = next(rule for rule in data["rules"] if rule.get("requires"))
                if mutation == "version":
                    data["version"] = 2
                elif mutation == "missing-rule":
                    data["rules"].pop()
                elif mutation == "duplicate-rule":
                    data["rules"][1] = data["rules"][0]
                elif mutation == "wrong-import":
                    importer["requires"][0]["logical-name"] = "missing"
                elif mutation == "wrong-provider":
                    provider["provides"][0]["logical-name"] = "wrong"
                elif mutation == "non-interface":
                    provider["provides"][0]["is-interface"] = False
                elif mutation == "wrong-source":
                    provider["provides"][0]["source-path"] = "wrong.cppm"
                else:
                    provider["primary-output"] = ""
                with self.assertRaises(RuntimeError):
                    runner.validate_scan(data, self.directory)

    def test_missing_tools_fail(self):
        for option in ("--compiler", "--scanner"):
            with self.subTest(option=option):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "run.py"), "--compiler", sys.executable,
                     option, str(ROOT / "missing-tool")],
                    text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("Required executable not found: " + str(ROOT / "missing-tool"),
                              result.stderr)
                self.assertNotIn("PASS:", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
