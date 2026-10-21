#!/usr/bin/env python3
"""Real-compiler generated action tests: --cxx /absolute/cxx [--compiler clang++]."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import pytest

pytestmark = pytest.mark.e2e

CXX = None
COMPILER = None
CONFIG = '[preset]\nname="llvm"\n[cpp]\nversion="17"\n'

# Every cached action declares this script and its data as inputs. The Python
# interpreter is an explicit system host tool, independent of the target compiler.
GENERATOR = r"""import os
import pathlib
import sys

kind, source, destination, adjustment = sys.argv[1:]
source = pathlib.Path(source)
destination = pathlib.Path(destination)
assert destination.is_absolute(), "Generator output must be absolute"
assert destination != pathlib.Path.cwd() / ".cxx/generated/code/main.cpp", "Output was not staged"
data = source.read_text(encoding="utf-8").strip()
if data == "missing":
    sys.exit(0)
if data == "fail":
    destination.write_text("Partial output\n", encoding="utf-8")
    sys.exit(23)
if data == "mutate":
    source.write_text("99\n", encoding="utf-8")
    data = "99"
value = int(data) + int(adjustment) + int(os.environ.get("CXX_GENERATED_TEST_VALUE", "0"))
if kind == "source":
    text = '#include <cstdio>\nint main() { puts("%d"); }\n' % value
elif kind == "header":
    text = '#pragma once\n#define VALUE %d\n' % value
else:
    assert kind == "data", "Unknown generator mode"
    text = "%d\n" % value
destination.write_text(text, encoding="utf-8")
"""

COUNTER = r"""import pathlib
import sys

# This deliberately undeclared side effect is legal only for an uncached action.
counter = pathlib.Path("invocations.txt")
value = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(value), encoding="utf-8")
pathlib.Path(sys.argv[1]).write_text(
    '#include <cstdio>\nint main() { puts("%d"); }\n' % value, encoding="utf-8")
"""


def cpp(value):
    """Quote a UTF-8 string for an ordinary C++ string literal, including paths."""
    return json.dumps(str(value), ensure_ascii=False)


@unittest.skipUnless(
    sys.platform.startswith("linux") or sys.platform == "darwin",
    "M1 generated actions cover Linux and macOS",
)
class GeneratedActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="cxx m1 generated ")
        cls.project = pathlib.Path(cls.temporary.name).resolve()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.env = os.environ.copy()
        self.env["CXX_BOOTSTRAP_COMPILER"] = COMPILER
        self.env.pop("CXX_BOOTSTRAP_ARCHIVER", None)
        self.env.pop("CXX_GENERATED_TEST_VALUE", None)
        # Retain only the host driver cache across cases, never generated artifacts.
        for path in self.project.iterdir():
            if path.name == ".cxx":
                for child in path.iterdir():
                    if child.name != "driver":
                        self.remove(child)
            else:
                self.remove(path)
        self.put("cxxrc.toml", CONFIG)
        self.put("generate script.py", GENERATOR)
        self.put("value data.txt", "7\n")

    def remove(self, path):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()

    def put(self, name, text):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def declare(self, body):
        self.put(
            "build.cpp",
            "#include <cxx/cxx.hpp>\nvoid build(cxx::Build& b) {\n" + body + "\n}\n",
        )

    def command(self, args, expected=0):
        result = subprocess.run(
            list(map(str, args)),
            cwd=self.project,
            env=self.env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if expected is None:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def invoke(self, *args, expected=0):
        return self.command([CXX, *args], expected)

    def assert_counts(self, result, custom, compiled=0, archived=0, linked=0):
        self.assert_custom_count(result, custom)
        match = re.search(r"compiled=(\d+) archived=(\d+) linked=(\d+)", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        self.assertEqual(
            tuple(map(int, match.groups())), (compiled, archived, linked), result.stderr
        )

    def assert_custom_count(self, result, expected):
        actual = sum(
            line.startswith("[custom] ") for line in result.stderr.splitlines()
        )
        self.assertEqual(
            actual,
            expected,
            "Expected [custom] execution records; reconcile a different runtime "
            "label with the parent task rather than guessing.\n" + result.stderr,
        )

    def generated(self, action="code", filename="main.cpp"):
        return self.project / ".cxx/generated" / action / filename

    def action(
        self,
        name="code",
        filename="main.cpp",
        kind="source",
        data="value data.txt",
        adjustment="0",
        tool=None,
    ):
        return (
            "auto& {name} = b.custom_action({quoted_name});\n"
            "auto {name}_file = {name}.output({filename});\n"
            '{name}.command({{{tool}, "generate script.py", {kind}, {data}, '
            "{name}_file.path(), {adjustment}}});\n"
            '{name}.input("generate script.py").input({data}).cacheable();\n'
        ).format(
            name=name,
            quoted_name=cpp(name),
            filename=cpp(filename),
            tool=cpp(tool or sys.executable),
            kind=cpp(kind),
            data=cpp(data),
            adjustment=cpp(adjustment),
        )

    def source_project(self, adjustment="0", tool=None):
        self.declare(
            self.action(adjustment=adjustment, tool=tool)
            + 'b.executable("app").source(code_file);'
        )

    def assert_run(self, value, custom, compiled=0, archived=0, linked=0, target="app"):
        result = self.invoke("run", target, "-v")
        self.assertEqual(result.stdout, str(value) + "\n", result.stderr)
        self.assert_counts(result, custom, compiled, archived, linked)
        return result

    def snapshot(self):
        # Include artifact bytes and existing .key metadata, but exclude the host
        # driver: changing build.cpp legitimately rebuilds that independent cache.
        files = {}
        for directory in ("generated", "build"):
            root = self.project / ".cxx" / directory
            for path in root.rglob("*"):
                if path.is_file():
                    files[str(path.relative_to(self.project))] = path.read_bytes()
        return files

    def test_clean_generated_source_and_stable_copied_handle(self):
        body = self.action()
        body += """auto copied = code_file;
const auto original_path = copied.path();
"""
        # Grow Build's action storage after copying a handle and taking a reference.
        for index in range(16):
            body += self.action(
                name="extra%d" % index, filename="value.txt", kind="data"
            )
        body += """if (copied.path() != original_path || code_file.path() != original_path)
    throw std::runtime_error("Generated handle path changed");
"""
        body += "if (copied.path() != " + cpp(self.generated()) + ")\n"
        body += '    throw std::runtime_error("Unexpected generated output path");\n'
        body += 'code.cacheable();\nb.executable("app").source(copied).depends_on(code_file);'
        self.declare(body)
        self.assertFalse(self.generated().exists())
        self.assert_run(7, 1, compiled=1, linked=1)
        self.assertTrue(self.generated().is_file())
        self.assertFalse(self.generated("extra0", "value.txt").exists())
        self.assertEqual(
            self.command([self.project / ".cxx/build/app/app"]).stdout, "7\n"
        )

    def test_cacheable_input_edit_and_noop(self):
        self.source_project()
        self.assert_run(7, 1, compiled=1, linked=1)
        self.assert_counts(self.invoke("build", "-v"), 0)
        self.put("value data.txt", "19\n")
        self.assert_run(19, 1, compiled=1, linked=1)
        self.assert_counts(self.invoke("build", "-v"), 0)

    def test_default_uncached_action_repeats_side_effect(self):
        self.put("counter.py", COUNTER)
        self.declare(
            'auto& code = b.custom_action("code");\n'
            'auto output = code.output("main.cpp");\n'
            "code.command({"
            + cpp(sys.executable)
            + ', "counter.py", output.path()});\n'
            'code.input("counter.py");\n'
            'b.executable("app").source(output);'
        )
        self.assert_run(1, 1, compiled=1, linked=1)
        self.assert_run(2, 1, compiled=1, linked=1)
        self.assertEqual((self.project / "invocations.txt").read_text(), "2")

    def test_generated_header_precedes_scan_through_indirect_libraries(self):
        self.put("leaf.cpp", "#include <value.hpp>\nint leaf() { return VALUE; }\n")
        self.put(
            "middle.cpp",
            "#include <value.hpp>\nint leaf();\n"
            "int value() { return leaf() + VALUE; }\n",
        )
        self.put(
            "main.cpp",
            "#include <value.hpp>\n#include <cstdio>\nint value();\n"
            'int main() { printf("%d\\n", value() + VALUE); }\n',
        )
        self.declare(
            self.action(name="header", filename="value.hpp", kind="header")
            + """
auto& leaf = b.static_library("leaf").source("leaf.cpp").depends_on(header_file)
    .include(".cxx/generated/header", cxx::Visibility::public_);
auto& middle = b.static_library("middle").source("middle.cpp").depends_on(header_file)
    .link(leaf, cxx::Visibility::public_);
b.executable("app").source("main.cpp").link(middle).depends_on(header_file);
"""
        )
        self.assert_run(21, 1, compiled=3, archived=2, linked=1)
        self.assert_counts(self.invoke("build", "-v"), 0)
        self.put("value data.txt", "8\n")
        self.assert_run(24, 1, compiled=3, archived=2, linked=1)

    def test_generated_chain_in_reverse_declaration_order(self):
        self.declare(
            'auto& code = b.custom_action("code");\n'
            'auto source = code.output("main.cpp");\n'
            + self.action(
                name="seed", filename="value.txt", kind="data", adjustment="2"
            )
            + "code.command({"
            + cpp(sys.executable)
            + ', "generate script.py", "source", seed_file.path(), source.path(), "3"});\n'
            'code.input("generate script.py").input(seed_file).cacheable();\n'
            'b.executable("app").source(source);'
        )
        result = self.assert_run(12, 2, compiled=1, linked=1)
        records = [
            line for line in result.stderr.splitlines() if line.startswith("[custom] ")
        ]
        self.assertIn("seed", records[0])
        self.assertIn("code", records[1])
        self.assert_counts(self.invoke("build", "-v"), 0)
        self.put("value data.txt", "10\n")
        self.assert_run(15, 2, compiled=1, linked=1)

    def test_selected_target_closure_and_default_all_actions(self):
        self.declare(
            self.action()
            + self.action(name="unrelated", filename="value.txt", kind="data")
            + 'b.executable("app").source(code_file);'
        )
        self.assert_run(7, 1, compiled=1, linked=1)
        other = self.generated("unrelated", "value.txt")
        self.assertFalse(other.exists())
        self.assert_counts(self.invoke("build", "-v"), 1)
        self.assertEqual(other.read_text(), "7\n")
        self.assert_counts(self.invoke("build", "-v"), 0)

    def test_script_argument_tool_path_and_environment_invalidation(self):
        self.source_project()
        self.assert_run(7, 1, compiled=1, linked=1)
        self.put(
            "generate script.py",
            GENERATOR.replace("value = int(data)", "value = 1 + int(data)"),
        )
        self.assert_run(8, 1, compiled=1, linked=1)
        self.source_project(adjustment="2")
        self.assert_run(10, 1, compiled=1, linked=1)
        self.assert_counts(self.invoke("build", "-v"), 0)
        # Exercise tool selection without modifying or copying a signed host binary.
        alternate = self.project / "alternate python"
        alternate.symlink_to(sys.executable)
        self.source_project(adjustment="2", tool=alternate)
        self.assert_run(10, 1)
        self.assert_counts(self.invoke("build", "-v"), 0)
        self.env["CXX_GENERATED_TEST_VALUE"] = "5"
        # The compiler and linker also fingerprint the process environment.
        self.assert_run(15, 1, compiled=1, linked=1)
        self.assert_counts(self.invoke("build", "-v"), 0)

    def test_corrupted_and_deleted_output_regenerates(self):
        self.source_project()
        self.assert_run(7, 1, compiled=1, linked=1)
        original = self.generated().read_bytes()
        self.generated().write_text("Corrupted generated C++\n", encoding="utf-8")
        self.assert_run(7, 1)
        self.assertEqual(self.generated().read_bytes(), original)
        self.generated().unlink()
        self.assert_run(7, 1)
        self.assertEqual(self.generated().read_bytes(), original)
        self.assert_counts(self.invoke("build", "-v"), 0)

    def test_failure_and_missing_output_preserve_artifacts_and_metadata(self):
        self.source_project()
        self.assert_run(7, 1, compiled=1, linked=1)
        before = self.snapshot()
        self.assertTrue(
            any(
                name.startswith(".cxx/generated/") and name.endswith(".key")
                for name in before
            ),
            "Expected generated action cache metadata",
        )
        for mode, diagnostic in (("fail", r"23|fail"), ("missing", r"output")):
            with self.subTest(mode=mode):
                self.put("value data.txt", mode + "\n")
                result = self.invoke("build", "-v", expected=None)
                self.assertRegex(result.stderr.lower(), diagnostic)
                self.assert_custom_count(result, 1)
                self.assertEqual(self.snapshot(), before, result.stderr)
                self.assertEqual(
                    self.command([self.project / ".cxx/build/app/app"]).stdout, "7\n"
                )
                self.put("value data.txt", "7\n")
                self.assert_counts(self.invoke("build", "-v"), 0)

    def test_input_mutation_during_generator_is_rejected(self):
        self.source_project()
        self.assert_run(7, 1, compiled=1, linked=1)
        before = self.snapshot()
        self.put("value data.txt", "mutate\n")
        result = self.invoke("build", "-v", expected=None)
        self.assertRegex(
            result.stderr.lower(), r"input.*(chang|mutat)|(chang|mutat).*input"
        )
        self.assert_custom_count(result, 1)
        self.assertEqual((self.project / "value data.txt").read_text(), "99\n")
        self.assertEqual(self.snapshot(), before, result.stderr)
        self.assertEqual(
            self.command([self.project / ".cxx/build/app/app"]).stdout, "7\n"
        )
        self.assert_run(99, 1, compiled=1, linked=1)
        self.assert_counts(self.invoke("build", "-v"), 0)

    def test_invalid_graphs_fail_before_any_action_execution(self):
        self.put("counter.py", COUNTER)
        self.put("main.cpp", "int main() { return 0; }\n")
        probe = (
            'auto& probe = b.custom_action("probe");\n'
            'auto probe_file = probe.output("probe.cpp");\n'
            "probe.command({"
            + cpp(sys.executable)
            + ', "counter.py", probe_file.path()});\n'
            'probe.input("counter.py");\n'
            'b.executable("app").source("main.cpp");\n'
        )
        pair = (
            'auto& first = b.custom_action("first");\n'
            'auto first_file = first.output("first.txt");\n'
            'auto& second = b.custom_action("second");\n'
            'auto second_file = second.output("second.txt");\n'
            "first.command({" + cpp(sys.executable) + ', "generate script.py", "data", '
            'second_file.path(), first_file.path(), "0"});\n'
            "second.command({"
            + cpp(sys.executable)
            + ', "generate script.py", "data", '
            'first_file.path(), second_file.path(), "0"});\n'
            'first.input("generate script.py").input(second_file);\n'
            'second.input("generate script.py").input(first_file);\n'
        )
        cases = [
            ("cycle", pair, r"cycle"),
            (
                "missing input",
                self.action(data="absent data.txt"),
                r"(missing|exist).*input|input.*(missing|exist)",
            ),
            (
                "multiple outputs",
                self.action() + 'code.output("second.cpp");',
                r"(one|single|already|multiple|duplicate).*output|output.*(one|single|already|multiple|duplicate)",
            ),
            (
                "embedded output argument",
                self.action()
                + "code.command({"
                + cpp(sys.executable)
                + ', "generate script.py", "--output=" + code_file.path()});',
                r"output|argument",
            ),
        ]
        for filename in (
            "",
            ".",
            "..",
            "../escape.cpp",
            "nested/main.cpp",
            "nested\\main.cpp",
            str(self.project / "absolute.cpp"),
        ):
            cases.append(
                (
                    "output " + repr(filename),
                    self.action(filename=filename),
                    r"output|filename",
                )
            )
        for consumer in (
            "probe.input(foreign_file);",
            'b.executable("foreign_source").source(foreign_file);',
            'b.executable("foreign_dependency").source("main.cpp").depends_on(foreign_file);',
        ):
            foreign = (
                "cxx::Build foreign(b.environment());\n"
                'auto& other = foreign.custom_action("other");\n'
                'auto foreign_file = other.output("other.cpp");\n'
            )
            cases.append(
                (
                    consumer,
                    foreign + consumer,
                    r"(different|another|foreign|owner|same).*build|build.*(different|another|foreign|owner|same)",
                )
            )
        for name, declaration, diagnostic in cases:
            with self.subTest(case=name):
                self.declare(probe + declaration)
                result = self.invoke("build", "-v", expected=None)
                self.assertRegex(result.stderr.lower(), diagnostic)
                self.assert_custom_count(result, 0)
                self.assertFalse(
                    (self.project / "invocations.txt").exists(), result.stderr
                )
                self.assertFalse(
                    self.generated("probe", "probe.cpp").exists(), result.stderr
                )
                self.assertFalse(
                    (self.project / ".cxx/build/app/app").exists(), result.stderr
                )
