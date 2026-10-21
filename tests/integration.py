#!/usr/bin/env python3
"""End-to-end tests against real Clang, without CMake/Ninja as a build backend."""

import hashlib
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

import pytest

pytestmark = pytest.mark.e2e

CONFIG = '[cxx]\nedition = "0"\n[cpp]\nversion = "17"\n[preset]\nname = "llvm"\nversion = "system"\n'
CXX = None
COMPILER = None


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="cxx integration ")
        cls.base = pathlib.Path(cls.temporary.name)
        cls.project = cls.base / "project with spaces"
        cls.project.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.env = os.environ.copy()
        self.env["CXX_BOOTSTRAP_COMPILER"] = COMPILER
        self.env.pop("CXX_BOOTSTRAP_ARCHIVER", None)
        for entry in self.project.iterdir():
            if entry.name == ".cxx":
                shutil.rmtree(entry / "build", ignore_errors=True)
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        self.put("cxxrc.toml", CONFIG)

    def put(self, name, text):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def invoke(self, *args, expected=0, cwd=None):
        result = subprocess.run(
            [str(CXX), *args],
            cwd=cwd or self.project,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if expected is None:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def counts(self, result):
        match = re.search(r"compiled=(\d+) archived=(\d+) linked=(\d+)", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return tuple(map(int, match.groups()))

    def simple(self, source="int main() { return 0; }\n"):
        self.put(
            "build.cpp",
            '#include <cxx/cxx.hpp>\nvoid build(cxx::Build& b) { b.executable("app").source("src/main.cpp"); }\n',
        )
        self.put("src/main.cpp", source)

    def test_compile_settings_and_invalidation(self):
        self.simple("""int main() {
    int unused_variable;
#ifdef __OPTIMIZE__
    return 2;
#else
    return 0;
#endif
}
""")
        for level, warnings, expected in (
            ("default_", False, 0),
            ("speed", True, 2),
            ("none", False, 0),
        ):
            with self.subTest(level=level):
                self.put(
                    "build.cpp",
                    "#include <cxx/cxx.hpp>\nvoid build(cxx::Build& b) { "
                    'b.executable("app").source("src/main.cpp")'
                    ".optimization(cxx::Optimization::" + level + ")"
                    ".warnings(" + str(warnings).lower() + "); }\n",
                )
                result = self.invoke("build", "-v")
                self.assertEqual(self.counts(result)[0], 1)
                commands = [
                    shlex.split(line[len("[command] ") :])
                    for line in result.stderr.splitlines()
                    if line.startswith("[command] ")
                ]
                compile_command = next(
                    cmd
                    for cmd in commands
                    if "-c" in cmd
                    and cmd[cmd.index("-c") + 1].endswith("/src/main.cpp")
                )
                for flag in ("-Wall", "-Wextra", "-Wpedantic"):
                    self.assertEqual(flag in compile_command, warnings)
                self.assertEqual("-O2" in compile_command, level == "speed")
                self.assertEqual("-O0" in compile_command, level == "none")
                self.assertEqual("unused variable" in result.stderr, warnings)
                self.assertEqual(
                    self.counts(self.invoke("run", expected=expected)), (0, 0, 0)
                )

    def test_system_include_visibility_and_dependencies(self):
        script = """#include <cxx/cxx.hpp>
void build(cxx::Build& b) {
    auto& leaf = b.static_library("leaf");
    leaf.source("leaf.cpp").warnings().optimization(cxx::Optimization::speed)
        .system_include("sys public", cxx::Visibility::public_)
        .system_include("sys private")
        .system_include("sys interface", cxx::Visibility::interface_);
    auto& middle = b.static_library("middle");
    middle.source("middle.cpp").link(leaf, cxx::Visibility::public_);
    b.executable("app").source("main.cpp").link(middle);
}
"""
        self.put("build.cpp", script)
        header = "inline int public_value() { return VALUE; }\nstatic void system_header_unused() {}\n"
        self.put("sys public/public.hpp", header.replace("VALUE", "1"))
        self.put("sys private/private.hpp", "#define PRIVATE_VALUE 0\n")
        self.put(
            "sys interface/interface.hpp",
            "inline int interface_value() { return 2; }\n",
        )
        self.put(
            "leaf.cpp",
            """#include <public.hpp>
#include <private.hpp>
#if __has_include(<interface.hpp>) || !defined(__OPTIMIZE__)
#error Incorrect local usage
#endif
int leaf() { return public_value() + PRIVATE_VALUE; }
""",
        )
        consumer = """#include <public.hpp>
#include <interface.hpp>
#if __has_include(<private.hpp>) || defined(__OPTIMIZE__)
#error Incorrect propagated usage
#endif
"""
        self.put(
            "middle.cpp",
            consumer
            + "int leaf(); int middle() { return leaf() + interface_value(); }\n",
        )
        self.put(
            "main.cpp",
            consumer
            + "int middle(); int main() { return middle() + public_value(); }\n",
        )
        result = self.invoke("build", "-v")
        self.assertNotIn("warning:", result.stderr)
        commands = [
            shlex.split(line[len("[command] ") :])
            for line in result.stderr.splitlines()
            if line.startswith("[command] ")
        ]
        for source in ("middle.cpp", "main.cpp"):
            command = next(
                cmd
                for cmd in commands
                if "-c" in cmd and cmd[cmd.index("-c") + 1].endswith("/" + source)
            )
            self.assertNotIn("-Wall", command)
            self.assertIn("-isystem", command)
        self.assertEqual(self.counts(self.invoke("run", "app", expected=4)), (0, 0, 0))
        self.put("sys public/public.hpp", header.replace("VALUE", "3"))
        self.assertEqual(self.counts(self.invoke("run", "app", expected=8))[0], 3)
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
        self.put(
            "build.cpp",
            script.replace('.system_include("sys public",', '.include("sys public",'),
        )
        result = self.invoke("build")
        self.assertEqual(self.counts(result)[0], 3)
        self.assertIn("unused function", result.stderr)

    def test_new_and_run_arguments(self):
        created = self.base / "new project"
        self.invoke("new", str(created))
        self.assertTrue((created / "build.cpp").is_file())
        self.assertEqual(self.invoke("run", cwd=created).stdout, "Hello, world!\n")
        self.invoke("new", str(created), expected=None)
        self.simple(
            "#include <iostream>\nint main(int argc, char** argv) { if (argc > 1) std::cout << argv[1]; return 7; }\n"
        )
        result = self.invoke("run", "--", "a;$(echo unsafe) space", expected=7)
        self.assertEqual(result.stdout, "a;$(echo unsafe) space")
        self.assertEqual(
            self.counts(self.invoke("build", cwd=self.project / "src")), (0, 0, 0)
        )

    def test_project_paths_and_script_headers(self):
        for name in ("中文项目", 'quote"project'):
            with self.subTest(path=name):
                root = self.base / name
                root.mkdir()
                (root / "cxxrc.toml").write_text(CONFIG)
                (root / "build.cpp").write_text(
                    '#include <cxx/cxx.hpp>\n#include "script.hpp"\n'
                    'void build(cxx::Build& b) { b.executable(APP_NAME).source("main.cpp"); }\n'
                )
                header = root / "script.hpp"
                header.write_text('#define APP_NAME "first"\n')
                (root / "main.cpp").write_text("int main() { return 0; }\n")
                self.invoke("run", cwd=root)
                self.assertTrue((root / ".cxx/build/first/first").is_file())
                self.assertEqual(self.counts(self.invoke("build", cwd=root)), (0, 0, 0))
                header.write_text('#define APP_NAME "second"\n')
                result = self.invoke("run", cwd=root)
                self.assertIn("[driver compile]", result.stderr)
                self.assertTrue((root / ".cxx/build/second/second").is_file())

    def test_incremental_source_header_driver_and_failures(self):
        self.put("declarations.hpp", '#pragma once\n#define APP_NAME "app"\n')
        self.put(
            "build.cpp",
            """#include <cxx/cxx.hpp>
#include "declarations.hpp"
void build(cxx::Build& b) {
    auto& lib = b.static_library("greet");
    lib.source("src/greet.cpp").include("include", cxx::Visibility::public_);
    b.executable(APP_NAME).source("src/main.cpp").link(lib);
}
""",
        )
        header = self.put(
            "include/greet.hpp", "#pragma once\n#define VALUE 1\nint greet();\n"
        )
        self.put(
            "src/greet.cpp", '#include "greet.hpp"\nint greet() { return VALUE; }\n'
        )
        self.put(
            "src/main.cpp",
            '#include "greet.hpp"\n#include <iostream>\nint main() { std::cout << greet(); }\n',
        )
        self.assertEqual(self.counts(self.invoke("build")), (2, 1, 1))
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
        self.put(
            "src/main.cpp",
            '#include "greet.hpp"\n#include <iostream>\nint main() { std::cout << greet() + 1; }\n',
        )
        self.assertEqual(self.counts(self.invoke("build")), (1, 0, 1))
        # Change contents without changing length or mtime.
        before = header.stat()
        header.write_text(header.read_text().replace("VALUE 1", "VALUE 2"))
        os.utime(header, ns=(before.st_atime_ns, before.st_mtime_ns))
        # Both raw header content and preprocessed output are tracked.
        self.assertEqual(self.counts(self.invoke("build")), (2, 1, 1))
        self.assertEqual(self.invoke("run").stdout, "3")
        self.put("declarations.hpp", '#pragma once\n#define APP_NAME "renamed"\n')
        result = self.invoke("build")
        self.assertIn("[driver compile]", result.stderr)
        self.assertTrue((self.project / ".cxx/build/renamed/renamed").is_file())
        executable = self.project / ".cxx/build/renamed/renamed"
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        self.put("src/main.cpp", "This is invalid C++!\n")
        self.invoke("build", expected=None)
        self.assertEqual(hashlib.sha256(executable.read_bytes()).hexdigest(), digest)
        self.assertEqual(subprocess.check_output([str(executable)], text=True), "3")

    def test_include_shadow_and_missing_candidate(self):
        self.put(
            "build.cpp",
            """#include <cxx/cxx.hpp>
void build(cxx::Build& b) {
    b.executable("app").source("src/main.cpp").include("first").include("second");
}
""",
        )
        self.put("second/choice.hpp", "#define CHOICE 1\n")
        self.put(
            "src/main.cpp",
            """#include <iostream>
#include <choice.hpp>
#if __has_include(<optional.hpp>)
#include <optional.hpp>
#else
#define OPTIONAL 0
#endif
int main() { std::cout << CHOICE + OPTIONAL; }
""",
        )
        self.assertEqual(self.invoke("run").stdout, "1")
        self.put("first/choice.hpp", "#define CHOICE 2\n")
        self.assertEqual(self.invoke("run").stdout, "2")
        self.put("first/optional.hpp", "#define OPTIONAL 4\n")
        self.assertEqual(self.invoke("run").stdout, "6")
        (self.project / "first/choice.hpp").unlink()
        self.assertEqual(self.invoke("run").stdout, "5")
        (self.project / "first/optional.hpp").unlink()
        self.assertEqual(self.invoke("run").stdout, "1")

    def test_visibility_diamond_and_selection(self):
        self.put(
            "build.cpp",
            """#include <cxx/cxx.hpp>
void build(cxx::Build& b) {
    auto& leaf = b.static_library("leaf");
    leaf.source("leaf.cpp").define("PUBLIC_VALUE=3", cxx::Visibility::public_).define("PRIVATE_VALUE=1");
    auto& left = b.static_library("left"); left.source("left.cpp").link(leaf, cxx::Visibility::public_);
    auto& right = b.static_library("right"); right.source("right.cpp").link(leaf);
    b.executable("app").source("main.cpp").link(left).link(right);
    b.executable("other").source("other.cpp");
}
""",
        )
        self.put("leaf.cpp", "int leaf() { return PUBLIC_VALUE + PRIVATE_VALUE; }\n")
        for side in ("left", "right"):
            self.put(
                side + ".cpp",
                "#ifdef PRIVATE_VALUE\n#error leaked private define\n#endif\nint leaf(); int "
                + side
                + "() { return leaf() + PUBLIC_VALUE; }\n",
            )
        self.put(
            "main.cpp",
            "#include <iostream>\n#ifdef PRIVATE_VALUE\n#error leaked private define\n#endif\nint left(); int right(); int main() { std::cout << left() + right() + PUBLIC_VALUE; }\n",
        )
        self.put("other.cpp", "int main() { return 0; }\n")
        self.assertEqual(self.invoke("run", "app").stdout, "17")
        self.assertFalse((self.project / ".cxx/build/other/other").exists())
        self.assertEqual(self.counts(self.invoke("build", "app")), (0, 0, 0))
        self.invoke("run", expected=None)
        self.assertIn(
            "exactly one executable", self.invoke("run", expected=None).stderr
        )

    def test_interface_usage(self):
        self.put(
            "build.cpp",
            """#include <cxx/cxx.hpp>
void build(cxx::Build& b) {
    auto& lib = b.static_library("lib");
    lib.source("lib.cpp").define("CLIENT_ONLY=5", cxx::Visibility::interface_);
    b.executable("app").source("main.cpp").link(lib);
}
""",
        )
        self.put(
            "lib.cpp",
            "#ifdef CLIENT_ONLY\n#error interface applied locally\n#endif\nint value() { return 0; }\n",
        )
        self.put(
            "main.cpp", "int value(); int main() { return value() + CLIENT_ONLY; }\n"
        )
        self.invoke("run", expected=5)

    def test_graph_and_script_errors(self):
        self.put("main.cpp", "int main() { return 0; }\n")
        cases = [
            ('b.executable("../escape").source("main.cpp");', "Invalid target name"),
            (
                'b.executable("x").source("main.cpp"); b.executable("x");',
                "Duplicate target",
            ),
            (
                'auto& a=b.static_library("a"); auto& c=b.static_library("c"); a.source("main.cpp").link(c); c.source("main.cpp").link(a);',
                "Dependency cycle",
            ),
            ('b.executable("x").source("missing.cpp");', "Missing source"),
            ('throw std::runtime_error("script failure");', "script failure"),
            ("throw 42;", "unknown exception"),
        ]
        for statement, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                self.put(
                    "build.cpp",
                    "#include <cxx/cxx.hpp>\nvoid build(cxx::Build& b) { "
                    + statement
                    + " }\n",
                )
                self.assertIn(diagnostic, self.invoke("build", expected=None).stderr)

    def test_config_schema_and_cli(self):
        self.simple()
        for config, diagnostic in [
            (CONFIG + "[unknown]\nx = 1\n", "Unsupported configuration"),
            (CONFIG.replace('"17"', "17"), "TOML string"),
            (CONFIG.replace('"system"', '"22"'), "downloads and Lock"),
            (CONFIG.replace('"llvm"', '"custom"'), "M0 requires llvm"),
            ("[broken", "cxx:"),
        ]:
            with self.subTest(config=config):
                self.put("cxxrc.toml", config)
                self.assertIn(diagnostic, self.invoke("build", expected=None).stderr)
        self.put("cxxrc.toml", CONFIG)
        for args in [
            ("build", "--locked"),
            ("build", "--"),
            ("unknown",),
            ("run", "app", "other"),
        ]:
            self.invoke(*args, expected=None)
        self.assertIn("Usage:", self.invoke("--help").stdout)
        self.assertIn("M0", self.invoke("--version").stdout)

    def test_script_modes_link_cpp23_runtime(self):
        for standard in ("17", "20", "23"):
            with self.subTest(standard=standard):
                self.put("cxxrc.toml", CONFIG.replace('"17"', '"' + standard + '"'))
                feature = {
                    "17": "static_assert(__cplusplus == 201703L);",
                    "20": "#include <concepts>\nstatic_assert(__cplusplus == 202002L);\nstatic_assert(std::integral<int>);",
                    "23": "static_assert(__cplusplus > 202002L);\nconstexpr int mode() { if consteval { return 23; } else { return 0; } }\nstatic_assert(mode() == 23);",
                }[standard]
                self.put(
                    "build.cpp",
                    "#include <cxx/cxx.hpp>\n"
                    + feature
                    + '''
void build(cxx::Build& b) {
    if (b.environment().compiler.empty() || b.environment().cpp_standard != "'''
                    + standard
                    + """") throw std::runtime_error("wrong environment");
    b.executable("app").source("main.cpp").cpp_standard("17");
}
""",
                )
                self.put(
                    "main.cpp",
                    "static_assert(__cplusplus == 201703L); int main() { return 0; }\n",
                )
                result = self.invoke("run", "-v")
                runtime_commands = [
                    line
                    for line in result.stderr.splitlines()
                    if line.startswith("[command]") and "/src/runtime.cpp" in line
                ]
                self.assertTrue(runtime_commands, result.stderr)
                self.assertTrue(
                    all('"-std=c++23"' in line for line in runtime_commands)
                )
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_internal_standard_is_separate_from_public_sdk(self):
        sdk = pathlib.Path(__file__).resolve().parents[1]
        source = self.put(
            "probe.cpp",
            '#include <cxx/cxx.hpp>\n#include "runtime.hpp"\nstatic_assert(__cplusplus == 201703L);\n',
        )
        command = [
            COMPILER,
            "-std=c++17",
            "-fsyntax-only",
            "-I" + str(sdk / "include"),
            "-I" + str(sdk / "src"),
            str(source),
        ]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        source.write_text('#include "support.hpp"\n')
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CXX internals require C++23", result.stderr)

    def test_output_corruption_recovery(self):
        self.simple()
        self.invoke("build")
        executable = self.project / ".cxx/build/app/app"
        executable.write_bytes(b"corrupt")
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 1))
        object_file = next((self.project / ".cxx/build/app/.objects").glob("*.o"))
        object_file.write_bytes(b"corrupt")
        result = self.invoke("build")
        self.assertEqual(self.counts(result)[0], 1)
        self.invoke("run")

    def test_source_location_matches_direct_compilation(self):
        self.put("cxxrc.toml", CONFIG.replace('"17"', '"20"'))
        self.simple("""#include <cstdio>
#include <source_location>
#define FIRST 123456789
int main() {
    printf("%d %u\\n", FIRST, std::source_location::current().column());
}
""")

        def direct():
            subprocess.run(
                [COMPILER, "-std=c++20", "src/main.cpp", "-o", "direct"],
                cwd=self.project,
                check=True,
                capture_output=True,
            )
            return subprocess.check_output([str(self.project / "direct")], text=True)

        first = direct()
        self.assertEqual(self.invoke("run").stdout, first)
        source = self.project / "src/main.cpp"
        source.write_text(source.read_text().replace("FIRST", "X"))
        second = direct()
        self.assertNotEqual(first, second)
        result = self.invoke("run")
        self.assertEqual(result.stdout, second)
        self.assertEqual(self.counts(result)[0], 1)

    def test_assembly_external_input_is_not_cached(self):
        import sys

        directive = (
            ".section __TEXT,__const\n.globl _payload\n_payload:"
            if sys.platform == "darwin"
            else ".section .rodata\n.globl payload\npayload:"
        )
        assembly = directive + '\n.incbin "data.bin"\n'
        import json

        self.simple(
            '#include <cstdio>\nconstexpr int digits = 1\'000;\nconst char* text = R"tag("asm()")tag";\nasm('
            + json.dumps(assembly)
            + ');\nextern "C" const unsigned char payload[];\nint main() { printf("%u", payload[0]); }\n'
        )
        data = self.project / "data.bin"
        data.write_bytes(bytes([1]))
        self.assertEqual(self.invoke("run").stdout, "1")
        data.write_bytes(bytes([2]))
        self.assertEqual(self.invoke("run").stdout, "2")

    def test_failed_metadata_publish_preserves_executable(self):
        self.simple()
        self.invoke("build")
        output = self.project / ".cxx/build/app/app"
        previous = output.read_bytes()
        key = output.with_name(output.name + ".key")
        key.unlink()
        key.mkdir()
        self.put("src/main.cpp", "int main() { return 7; }\n")
        self.invoke("build", expected=None)
        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(subprocess.run([str(output)]).returncode, 0)

    def test_dependency_filename_escaping(self):
        self.put(
            "build.cpp",
            '#include <cxx/cxx.hpp>\nvoid build(cxx::Build& b) { b.executable("app").source("src/unit $ #.cpp"); }\n',
        )
        self.put("src/header $ #.hpp", "#define RESULT 0\n")
        self.put(
            "src/unit $ #.cpp",
            '#include "header $ #.hpp"\nint main() { return RESULT; }\n',
        )
        self.invoke("run")
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
        self.put("src/header $ #.hpp", "#define RESULT 3\n")
        self.invoke("run", expected=3)

    def test_compiler_content_change(self):
        self.simple()
        self.env.pop("CXX_BOOTSTRAP_COMPILER")
        compiler = COMPILER
        wrapper = self.put(
            "compiler wrapper", "#!/bin/sh\nexec " + shlex.quote(compiler) + ' "$@"\n'
        )
        wrapper.chmod(0o755)
        self.put("cxxrc.toml", CONFIG + '\n[compiler]\npath = "compiler wrapper"\n')
        self.invoke("build")
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
        with wrapper.open("a") as stream:
            stream.write("# Changed tool identity\n")
        self.assertEqual(self.counts(self.invoke("build"))[0], 1)
