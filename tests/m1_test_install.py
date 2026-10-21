#!/usr/bin/env python3
"""Real-compiler test/install tests: --cxx /absolute/cxx [--compiler clang++]."""

import json
import os
import pathlib
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
SHARED_SUFFIX = ".dylib" if sys.platform == "darwin" else ".so"
APP = 'auto& app = b.executable("app").source("main.cpp");\n'


def cpp(value):
    return json.dumps(str(value), ensure_ascii=False)


@unittest.skipUnless(
    sys.platform.startswith("linux") or sys.platform == "darwin",
    "M1 test/install covers Linux and macOS",
)
class TestInstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="cxx m1 test install ")
        cls.project = pathlib.Path(cls.temporary.name).resolve() / "project"
        cls.project.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.env = os.environ.copy()
        self.env["CXX_BOOTSTRAP_COMPILER"] = COMPILER
        self.env.pop("CXX_BOOTSTRAP_ARCHIVER", None)
        for name in list(self.env):
            if name.startswith(("LD_", "DYLD_")):
                del self.env[name]
        # Keep only the host driver cache between independent project fixtures.
        for path in self.project.parent.iterdir():
            if path == self.project:
                for child in path.iterdir():
                    if child.name == ".cxx":
                        for artifact in child.iterdir():
                            if artifact.name != "driver":
                                self.remove(artifact)
                    else:
                        self.remove(child)
            else:
                self.remove(path)
        self.put("cxxrc.toml", CONFIG)
        self.put("main.cpp", '#include <cstdio>\nint main() { puts("installed"); }\n')
        self.put("payload.txt", "Original payload\n")
        self.prefix = self.project.parent / "install prefix"

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

    def command(self, args, expected=0, cwd=None):
        result = subprocess.run(
            list(map(str, args)),
            cwd=cwd or self.project,
            env=self.env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        if expected is None:
            self.assertNotEqual(result.returncode, 0, output)
        else:
            self.assertEqual(result.returncode, expected, output)
        return result

    def invoke(self, *args, expected=0, cwd=None):
        return self.command([CXX, *args], expected, cwd)

    def install(self, expected=0):
        return self.invoke("install", "--prefix", self.prefix, "-v", expected=expected)

    def assert_no_compile(self, result):
        # Compile counters already form part of the native build summary. Install
        # may perform separate links; their count is deliberately not prescribed.
        self.assertRegex(result.stderr, r"\bcompiled=0\b")

    def snapshot(self, root):
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def assert_rejected_without_writes(self, diagnostic):
        self.prefix.mkdir(exist_ok=True)
        sentinel = self.prefix / "unrelated.txt"
        sentinel.write_text("Keep me\n", encoding="utf-8")
        before = self.snapshot(self.prefix)
        entries = sorted(
            str(path.relative_to(self.prefix)) for path in self.prefix.rglob("*")
        )
        result = self.install(expected=None)
        self.assertRegex(result.stderr.lower(), diagnostic)
        self.assertNotIn("[install]", result.stderr)
        self.assertEqual(self.snapshot(self.prefix), before)
        self.assertEqual(
            sorted(
                str(path.relative_to(self.prefix)) for path in self.prefix.rglob("*")
            ),
            entries,
        )
        return result

    def test_tests_repeat_with_literal_arguments_and_project_cwd(self):
        arguments = [
            "space argument",
            "$(touch shell-marker)",
            "; touch shell-marker",
            "*",
            "",
        ]
        self.put(
            "main.cpp",
            """#include <filesystem>
#include <fstream>
#include <string>
#include <vector>
int main(int argc, char** argv) {
    const std::vector<std::string> expected = {%s};
    if (argc != static_cast<int>(expected.size()) + 1) return 11;
    for (int i = 1; i < argc; ++i) if (argv[i] != expected[i - 1]) return 12;
    if (std::filesystem::current_path() != std::filesystem::path(%s)) return 13;
    std::ofstream("executions.txt", std::ios::app) << "ran\\n";
}
"""
            % (", ".join(map(cpp, arguments)), cpp(self.project)),
        )
        self.declare(
            APP + 'b.test("smoke", app, {' + ", ".join(map(cpp, arguments)) + "});"
        )
        subdir = self.project / "nested"
        subdir.mkdir()
        self.invoke("test", "-v", cwd=subdir)
        repeated = self.invoke("test")
        self.assert_no_compile(repeated)
        self.assertEqual((self.project / "executions.txt").read_text(), "ran\nran\n")
        self.assertFalse((self.project / "shell-marker").exists())

    def test_filter_is_literal_substring_and_builds_only_selected_closure(self):
        self.put("dep.cpp", "int value() { return 0; }\n")
        self.put("main.cpp", "int value(); int main() { return value(); }\n")
        self.put("broken.cpp", "#error Unselected target must not compile\n")
        self.declare("""auto& dep = b.static_library("dep").source("dep.cpp");
auto& app = b.executable("app").source("main.cpp").link(dep);
auto& broken = b.executable("broken").source("broken.cpp");
b.test("prefix [smoke].* suffix", app);
b.test("prefix smoke-other suffix", broken);
b.executable("unregistered").source("broken.cpp");
""")
        self.invoke("test", "[smoke].*", "-v")
        self.assertTrue((self.project / ".cxx/build/app/app").is_file())
        for target in ("broken", "unregistered"):
            self.assertFalse((self.project / ".cxx/build" / target).exists())
        repeated = self.invoke("test", "[smoke].*")
        self.assert_no_compile(repeated)
        result = self.invoke("test", "absent-filter", expected=None)
        self.assertRegex(result.stderr.lower(), r"match|filter|no test")

    def test_tests_fail_fast_and_preserve_program_exit(self):
        self.put(
            "main.cpp",
            "#include <fstream>\nint main(int argc, char**) {\n"
            'if (argc == 1) return 37;\nstd::ofstream("later-ran") << "bad";\n}\n',
        )
        self.declare(APP + 'b.test("first", app);\nb.test("later", app, {"later"});')
        self.invoke("test", "-v", expected=37)
        self.assertFalse((self.project / "later-ran").exists())
        self.invoke("test", expected=37)
        self.assertFalse((self.project / "later-ran").exists())

    def test_cli_errors_and_no_registered_tests(self):
        self.declare(APP)
        result = self.invoke("test", expected=None)
        self.assertRegex(result.stderr.lower(), r"no test|register|empty")
        self.declare(APP + 'b.test("smoke", app); b.install(app);')
        cases = [
            (("install",), r"prefix"),
            (("install", "--prefix"), r"prefix|argument|value"),
            (
                ("install", "--prefix", self.prefix, "--prefix", self.prefix),
                r"prefix|duplicate",
            ),
            (
                ("install", "--prefix", self.prefix, "app"),
                r"argument|selector|unexpected|unknown",
            ),
            (("install", "--prefix", self.prefix, "--unknown"), r"unknown|option"),
            (("test", "--unknown"), r"unknown|option"),
            (("test", "smoke", "extra"), r"argument|filter|unexpected"),
        ]
        for args, diagnostic in cases:
            with self.subTest(args=args):
                result = self.invoke(*args, expected=None)
                self.assertRegex(result.stderr.lower(), diagnostic)
                self.assertFalse(self.prefix.exists())

    def test_install_opt_in_layout_relative_prefix_and_file_destination(self):
        self.put("unit.cpp", "int value() { return 2; }\n")
        self.put("broken.cpp", "#error Unselected target must not compile\n")
        self.declare(
            APP
            + """auto& archive = b.static_library("archive").source("unit.cpp");
auto& shared = b.shared_library("shared").source("unit.cpp");
b.executable("unselected").source("broken.cpp");
b.install(app); b.install(archive); b.install(shared);
b.install_file("payload.txt", "share/nested/renamed.txt");
"""
        )
        subdir = self.project / "nested"
        subdir.mkdir()
        self.invoke("install", "--prefix", "local prefix", "-v", cwd=subdir)
        prefix = self.project / "local prefix"
        self.assertEqual(
            set(self.snapshot(prefix)),
            {
                "bin/app",
                "lib/libarchive.a",
                "lib/libshared" + SHARED_SUFFIX,
                "share/nested/renamed.txt",
            },
        )
        self.assertEqual(
            (prefix / "share/nested/renamed.txt").read_text(), "Original payload\n"
        )
        app = prefix / "bin/app"
        self.assertTrue(os.access(app, os.X_OK))
        self.assertEqual(self.command([app]).stdout, "installed\n")
        self.assertFalse((self.project / ".cxx/build/unselected").exists())

    def test_install_private_shared_chain_relocates_without_build_tree(self):
        self.declare("""auto& obj = b.object_library("obj").source("obj.cpp");
auto& archive = b.static_library("archive").link(obj);
auto& leaf = b.shared_library("leaf").source("leaf.cpp").link(archive);
auto& middle = b.shared_library("middle").source("middle.cpp").link(leaf);
auto& top = b.shared_library("top").source("top.cpp").link(middle);
auto& app = b.executable("app").source("main.cpp").link(top);
b.install(app);
""")
        self.put("obj.cpp", "int object_value() { return 4; }\n")
        self.put(
            "leaf.cpp",
            "int object_value(); int leaf() { return object_value() + 1; }\n",
        )
        self.put("middle.cpp", "int leaf(); int middle() { return leaf() + 2; }\n")
        self.put("top.cpp", "int middle(); int top() { return middle() + 3; }\n")
        self.put(
            "main.cpp",
            '#include <cstdio>\nint top(); int main() { printf("%d\\n", top()); }\n',
        )
        self.assertEqual(self.invoke("run", "app").stdout, "10\n")
        ordinary = self.snapshot(self.project / ".cxx/build")
        self.install()
        self.assertEqual(self.snapshot(self.project / ".cxx/build"), ordinary)
        self.assertEqual(self.invoke("run", "app").stdout, "10\n")
        expected = {"bin/app"} | {
            "lib/lib" + name + SHARED_SUFFIX for name in ("leaf", "middle", "top")
        }
        self.assertEqual(set(self.snapshot(self.prefix)), expected)
        staged = self.snapshot(self.project / ".cxx/install")
        self.assertTrue(expected <= staged.keys(), staged.keys())
        self.assertEqual(
            (self.prefix / "bin/app").stat().st_mode & 0o777,
            (self.project / ".cxx/install/bin/app").stat().st_mode & 0o777,
        )
        relocated = self.project.parent / "relocated prefix"
        self.prefix.rename(relocated)
        hidden = self.project.parent / "unavailable project"
        self.project.rename(hidden)
        try:
            self.assertEqual(
                self.command([relocated / "bin/app"], cwd=relocated).stdout, "10\n"
            )
        finally:
            hidden.rename(self.project)

    def test_install_shared_root_includes_private_runtime_dependencies(self):
        self.put("leaf.cpp", "int leaf() { return 8; }\n")
        self.put("top.cpp", "int leaf(); int top() { return leaf(); }\n")
        self.declare("""auto& leaf = b.shared_library("leaf").source("leaf.cpp");
auto& top = b.shared_library("top").source("top.cpp").link(leaf);
b.install(top);
""")
        self.install()
        self.assertEqual(
            set(self.snapshot(self.prefix)),
            {"lib/libtop" + SHARED_SUFFIX, "lib/libleaf" + SHARED_SUFFIX},
        )
        # A real external consumer verifies the shared-root installation too.
        consumer = self.put(
            "consumer.cpp", "int top(); int main() { return top() == 8 ? 0 : 9; }\n"
        )
        executable = self.prefix / "consumer"
        self.command(
            [
                COMPILER,
                consumer,
                "-L" + str(self.prefix / "lib"),
                "-ltop",
                "-Wl,-rpath," + str(self.prefix / "lib"),
                "-o",
                executable,
            ]
        )
        hidden = self.project.parent / "unavailable project"
        self.project.rename(hidden)
        try:
            self.command([executable], cwd=self.prefix)
        finally:
            hidden.rename(self.project)

    def test_reinstall_repairs_changed_deleted_files_without_compilation(self):
        self.declare(
            APP + 'b.install(app); b.install_file("payload.txt", "share/data.txt");'
        )
        self.install()
        original = self.snapshot(self.prefix)
        app = self.prefix / "bin/app"
        data = self.prefix / "share/data.txt"
        unrelated = self.prefix / "unrelated.txt"
        unrelated.write_text("Keep me\n", encoding="utf-8")
        app.write_bytes(b"Broken executable")
        app.chmod(0o600)
        data.unlink()
        self.assert_no_compile(self.install())
        for name, content in original.items():
            self.assertEqual((self.prefix / name).read_bytes(), content)
        self.assertTrue(os.access(app, os.X_OK))
        self.assertEqual(self.command([app]).stdout, "installed\n")
        app.unlink()
        data.write_text("Broken payload\n", encoding="utf-8")
        self.assert_no_compile(self.install())
        self.assertEqual(app.read_bytes(), original["bin/app"])
        self.assertEqual(data.read_bytes(), original["share/data.txt"])
        self.assertEqual(unrelated.read_text(), "Keep me\n")
        self.put("payload.txt", "Updated payload\n")
        self.assert_no_compile(self.install())
        self.assertEqual(data.read_text(), "Updated payload\n")

    def test_generated_header_install_only_builds_producer(self):
        self.put(
            "generate.py",
            "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('#pragma once\\n#define VALUE 17\\n')\n",
        )
        self.put("broken.cpp", "#error Unrelated target must not compile\n")
        self.declare(
            """auto& generator = b.custom_action("header");
auto header = generator.output("original.hpp");
generator.command({%s, "generate.py", header.path()}).input("generate.py").cacheable();
b.executable("unrelated").source("broken.cpp");
b.install_file(header, "include/api/renamed.hpp");
"""
            % cpp(sys.executable)
        )
        self.install()
        destination = self.prefix / "include/api/renamed.hpp"
        self.assertEqual(destination.read_text(), "#pragma once\n#define VALUE 17\n")
        self.assertFalse((self.project / ".cxx/build/unrelated").exists())
        generated = self.project / ".cxx/generated/header/original.hpp"
        self.assertTrue(generated.is_file())
        mtime = generated.stat().st_mtime_ns
        destination.unlink()
        self.assert_no_compile(self.install())
        self.assertEqual(destination.read_bytes(), generated.read_bytes())
        self.assertEqual(generated.stat().st_mtime_ns, mtime)

    def test_invalid_declarations_prevent_all_destination_writes(self):
        foreign = "cxx::Build foreign(b.environment());\n"
        cases = [
            (
                'b.install_file("payload.txt", "../escaped.txt");',
                r"relative|travers|destination|\.\.",
            ),
            (
                'b.install_file("payload.txt", %s);'
                % cpp(self.project.parent / "escaped.txt"),
                r"relative|absolute|destination",
            ),
            ("b.install(app); b.install(app);", r"duplicate|already"),
            ('b.test("same", app); b.test("same", app);', r"duplicate|already"),
            (
                'auto& archive = b.static_library("archive").source("main.cpp"); b.test("invalid", archive);',
                r"executable|test|kind",
            ),
            (
                'b.install_file("payload.txt", "include/Name.hpp"); b.install_file("payload.txt", "include/name.hpp");',
                r"collision|duplicate|conflict",
            ),
            (
                'auto& obj = b.object_library("obj").source("main.cpp"); b.install(obj);',
                r"object|kind|install",
            ),
            (
                foreign
                + 'auto& other = foreign.executable("other").source("main.cpp"); b.install(other);',
                r"owner|foreign|another|different|same build",
            ),
            (
                foreign
                + 'auto& gen = foreign.custom_action("gen"); auto file = gen.output("x.hpp"); b.install_file(file, "include/x.hpp");',
                r"owner|foreign|another|different|same build",
            ),
            (
                foreign
                + 'auto& other = foreign.executable("other").source("main.cpp"); b.test("foreign", other);',
                r"owner|foreign|another|different|same build",
            ),
            (
                'b.install_file("missing.txt", "share/missing.txt");',
                r"missing|exist|source",
            ),
        ]
        for body, diagnostic in cases:
            with self.subTest(declaration=body):
                self.declare(
                    APP + 'b.install_file("payload.txt", "share/valid.txt");\n' + body
                )
                self.assert_rejected_without_writes(diagnostic)
                self.assertFalse((self.project.parent / "escaped.txt").exists())

    def test_prefix_collisions_symlinks_and_source_overlap(self):
        self.declare(
            APP + 'b.install(app); b.install_file("payload.txt", "share/data.txt");'
        )
        self.prefix.mkdir()
        (self.prefix / "bin").write_text("Blocking file\n", encoding="utf-8")
        self.assert_rejected_without_writes(r"directory|collision|conflict|prefix|path")
        (self.prefix / "bin").unlink()
        (self.prefix / "bin/app").mkdir(parents=True)
        self.assert_rejected_without_writes(
            r"directory|collision|conflict|file|path type"
        )
        self.remove(self.prefix / "bin")
        outside = self.project.parent / "outside"
        outside.mkdir()
        (outside / "app").write_text("Keep outside\n", encoding="utf-8")
        (self.prefix / "bin").symlink_to(outside, target_is_directory=True)
        self.assert_rejected_without_writes(r"symlink|symbolic")
        self.assertEqual((outside / "app").read_text(), "Keep outside\n")
        (self.prefix / "bin").unlink()
        (self.prefix / "bin").mkdir()
        (self.prefix / "bin/app").symlink_to(outside / "app")
        self.assert_rejected_without_writes(r"symlink|symbolic")
        self.assertEqual((outside / "app").read_text(), "Keep outside\n")
        self.remove(self.prefix)
        self.prefix.symlink_to(outside, target_is_directory=True)
        result = self.install(expected=None)
        self.assertRegex(result.stderr.lower(), r"symlink|symbolic")
        self.assertEqual(self.snapshot(outside), {"app": b"Keep outside\n"})
        self.prefix.unlink()
        self.declare('b.install_file("payload.txt", "payload.txt");')
        result = self.invoke("install", "--prefix", self.project, expected=None)
        self.assertRegex(result.stderr.lower(), r"overlap|source|same|prefix")
        self.assertEqual(
            (self.project / "payload.txt").read_text(), "Original payload\n"
        )
        self.declare(APP + 'b.install(app); b.install_file("payload.txt", "bin/app");')
        self.assert_rejected_without_writes(r"collision|duplicate|conflict")

    def assert_project_install_rejected_without_writes(self, diagnostic):
        def snapshot():
            return {
                str(path.relative_to(self.project)): path.read_bytes()
                if path.is_file()
                else None
                for path in self.project.rglob("*")
                if ".cxx" not in path.relative_to(self.project).parts
            }

        before = snapshot()
        result = self.invoke("install", "--prefix", ".", "-v", expected=None)
        self.assertRegex(result.stderr.lower(), diagnostic)
        self.assertNotIn("[install]", result.stderr)
        self.assertEqual(snapshot(), before)
        return result

    def test_install_protects_transitive_build_script_headers_on_cache_hit(self):
        self.put("build.cpp", '#include <cxx/cxx.hpp>\n#include "bridge.hpp"\n')
        self.put("bridge.hpp", '#include "rules.hpp"\n')
        self.put(
            "rules.hpp",
            "void build(cxx::Build& b) {\n"
            'b.install_file("payload.txt", "share/first.txt");\n'
            'b.install_file("payload.txt", "rules.hpp");\n}\n',
        )
        self.assert_project_install_rejected_without_writes(r"overlap|source")
        # A cache hit must recreate fresh protection data, not reuse a stale handoff.
        (self.project / ".cxx/driver/inputs").unlink()
        cached = self.assert_project_install_rejected_without_writes(r"overlap|source")
        self.assertNotIn("[driver compile]", cached.stderr)
        self.assertNotIn("[driver link]", cached.stderr)

    def test_install_protects_target_headers_on_cache_hit(self):
        self.put("value.hpp", "inline int value() { return 0; }\n")
        self.put("main.cpp", '#include "value.hpp"\nint main() { return value(); }\n')
        self.declare(
            APP + 'b.install_file("payload.txt", "share/first.txt"); b.install(app);\n'
            'b.install_file("payload.txt", "value.hpp");'
        )
        self.assert_project_install_rejected_without_writes(r"overlap|source")
        cached = self.assert_project_install_rejected_without_writes(r"overlap|source")
        self.assertNotIn("[compile]", cached.stderr)
        self.assertNotIn("[driver compile]", cached.stderr)

    def test_install_rejects_lossy_build_script_scan_before_any_writes(self):
        original = self.project
        lossy = original.parent / "lossy\\project"
        original.rename(lossy)
        self.project = lossy
        try:
            self.put("build.cpp", '#include <cxx/cxx.hpp>\n#include "rules.hpp"\n')
            self.put(
                "rules.hpp",
                "void build(cxx::Build& b) {\n"
                'b.install_file("payload.txt", "share/first.txt");\n'
                'b.install_file("payload.txt", "rules.hpp");\n}\n',
            )
            self.invoke("build")  # Ordinary builds still support lossy path spellings.
            self.assert_project_install_rejected_without_writes(
                r"incomplete|lossy|protection"
            )
            self.assert_rejected_without_writes(r"incomplete|lossy|protection")
        finally:
            lossy.rename(original)
            self.project = original

    def test_install_rejects_lossy_project_before_overwriting_target_header(self):
        original = self.project
        lossy = original.parent / "lossy\\project"
        original.rename(lossy)
        self.project = lossy
        try:
            self.put("value.hpp", "inline int value() { return 0; }\n")
            self.put(
                "main.cpp", '#include "value.hpp"\nint main() { return value(); }\n'
            )
            self.declare(
                APP
                + 'b.install_file("payload.txt", "share/first.txt"); b.install(app);\n'
                'b.install_file("payload.txt", "value.hpp");'
            )
            self.invoke("build")
            self.assert_project_install_rejected_without_writes(
                r"incomplete|lossy|protection"
            )
        finally:
            lossy.rename(original)
            self.project = original

    def test_install_rejects_lossy_target_scan_before_any_writes(self):
        # Only the target includes a lossy path; bootstrap scanning is complete.
        self.put("lossy\\headers/value.hpp", "inline int value() { return 0; }\n")
        self.put("main.cpp", '#include "value.hpp"\nint main() { return value(); }\n')
        self.declare(
            'auto& app = b.executable("app").source("main.cpp").include("lossy\\\\headers");\n'
            'b.install_file("payload.txt", "share/first.txt"); b.install(app);'
        )
        self.invoke("build")
        self.assert_rejected_without_writes(r"incomplete|lossy|protection")
        self.assertFalse((self.prefix / "share").exists())

    def test_compile_and_generator_failure_prevent_destination_writes(self):
        self.put("main.cpp", "#error Deliberate compile failure\n")
        self.declare(
            APP + 'b.install_file("payload.txt", "share/valid.txt"); b.install(app);'
        )
        self.assert_rejected_without_writes(r"error|compil|failed")
        self.put(
            "fail.py",
            "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('Partial output')\nsys.exit(23)\n",
        )
        self.declare(
            """b.install_file("payload.txt", "share/valid.txt");
auto& generator = b.custom_action("failure");
auto header = generator.output("bad.hpp");
generator.command({%s, "fail.py", header.path()}).input("fail.py").cacheable();
b.install_file(header, "include/bad.hpp");
"""
            % cpp(sys.executable)
        )
        self.assert_rejected_without_writes(r"23|fail|custom|action")
