#!/usr/bin/env python3
"""Focused execution regressions; run with --cxx /absolute/cxx [--compiler clang++]."""

import errno
import os
import pathlib
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import pytest

pytestmark = pytest.mark.e2e

CXX = None
COMPILER = None
TIMEOUT = 120
CONFIG = '[preset]\nname="llvm"\n[cpp]\nversion="17"\n'


def stop_tree(pid):
    """Freeze descendants before killing: CXX creates more than one process group."""
    known = {pid}
    while True:
        for child in known:
            try:
                os.kill(child, signal.SIGSTOP)
            except ProcessLookupError:
                pass
        rows = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid="], text=True, timeout=5
        ).splitlines()
        found = set(known)
        for row in rows:
            child, parent = map(int, row.split())
            if parent in known:
                found.add(child)
        if found == known:
            break
        known = found
    for child in known - {pid}:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# Remain alive after reporting the result so the controller can clean up even
# stopped descendants. This is a tiny foreground shell, not the program under test.
PTY_SHELL = r"""
import os, resource, signal, subprocess, sys, time
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
# A caught handler resets on exec; unlike SIG_IGN it does not disable Ctrl-C in CXX.
signal.signal(signal.SIGINT, lambda *args: None)
command = subprocess.Popen([sys.argv[1], "run", "--", sys.argv[2]],
                           stdin=subprocess.DEVNULL if sys.argv[3] == "redirect" else None,
                           preexec_fn=os.setpgrp if sys.argv[2] == "background" else None)
print("CXX-PID:%d" % command.pid, flush=True)
code = command.wait(timeout=60)
print("EXIT:%d FOREGROUND:%d" % (code, os.tcgetpgrp(0) == os.getpgrp()), flush=True)
print("SHELL-INPUT:", flush=True)
line = sys.stdin.readline().strip()
print("SHELL-READ:" + line, flush=True)
time.sleep(120)
"""


@unittest.skipUnless(os.name == "posix", "POSIX process and symlink tests")
class Regressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="cxx regressions ")
        cls.project = pathlib.Path(cls.temporary.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.env = os.environ.copy()
        self.env["CXX_BOOTSTRAP_COMPILER"] = COMPILER
        self.env.pop("CXX_BOOTSTRAP_ARCHIVER", None)
        # Reuse only the host runtime compilation across these focused cases.
        for path in self.project.iterdir():
            if path.name == ".cxx":
                shutil.rmtree(path / "build", ignore_errors=True)
            elif path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        self.put("cxxrc.toml", CONFIG)

    def put(self, name, text):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def declare(self, body='b.executable("app").source("main.cpp");'):
        self.put(
            "build.cpp",
            "#include <cxx/cxx.hpp>\nvoid build(cxx::Build& b){" + body + "}\n",
        )

    def command(self, args, expected=0):
        process = subprocess.Popen(
            list(map(str, args)),
            cwd=self.project,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            stop_tree(process.pid)
            stdout, stderr = process.communicate(timeout=5)
            self.fail("Command timed out: %r\n%s%s" % (args, stdout, stderr))
        finally:
            if process.poll() is None:
                stop_tree(process.pid)
                process.wait(timeout=5)
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if expected is None:
            self.assertNotEqual(result.returncode, 0, stdout + stderr)
        else:
            self.assertEqual(result.returncode, expected, stdout + stderr)
        return result

    def invoke(self, *args, expected=0):
        return self.command([CXX, *args], expected)

    def direct(self, source="main.cpp", expected=0, flags=()):
        output = self.project / "direct"
        self.command([COMPILER, "-std=c++17", *flags, source, "-o", output])
        return self.command([output], expected)

    def check_backslash_inputs(self, source, changed, decoy, reason, flags=()):
        preprocess = [COMPILER, "-std=c++17", *flags, "-E", source]
        original = changed.read_text()
        first_text = self.command(preprocess).stdout
        first = self.invoke("run", "-v")
        self.assertEqual(first.stdout, self.direct(source, flags=flags).stdout)

        changed.write_text(original.replace("(void)LONG_MACRO", "(void)X"))
        self.assertEqual(
            first_text,
            self.command(preprocess).stdout,
            "Fixture must preserve preprocessed bytes",
        )
        direct = self.direct(source, flags=flags)
        self.assertNotEqual(
            first.stdout, direct.stdout, "Fixture must change the source column"
        )
        incremental = self.invoke("run")
        self.assertEqual(incremental.stdout, direct.stdout)
        self.assertIn("compiled=1 archived=0 linked=1", incremental.stderr)
        self.assertIn("[uncached]", first.stderr)
        self.assertIn(reason, first.stderr)
        unchanged = self.invoke("build")
        self.assertIn("compiled=1 archived=0 linked=0", unchanged.stderr)

        # Lossy depfile names may also refer to missing files. The legal POSIX
        # input still compiles, and restoring its bytes must restore its result.
        decoy.unlink()
        changed.write_text(original)
        without_decoy = self.invoke("run")
        self.assertEqual(without_decoy.stdout, self.direct(source, flags=flags).stdout)
        self.assertEqual(without_decoy.stdout, first.stdout)
        self.assertIn("compiled=1", without_decoy.stderr)

    def test_global_symbol_declaration_preserves_noop_build(self):
        self.declare()
        for directive in (
            '".globl cxx_probe_symbol"',
            '".globl " "cxx_probe_symbol"',
            '".glo" "bl cxx_probe_symbol"',
        ):
            with self.subTest(directive=directive):
                self.put(
                    "main.cpp",
                    "asm(" + directive + ");\n"
                    'extern "C" int probe() asm("cxx_probe_symbol");\n'
                    "int probe() { return 0; }\nint main() { return probe(); }\n",
                )
                self.invoke("run")
                unchanged = self.invoke("build", "-v")
                self.assertIn("compiled=0 archived=0 linked=0", unchanged.stderr)
                self.assertNotIn("[uncached]", unchanged.stderr)

    def test_global_symbol_prefix_keeps_assembler_inputs_uncached(self):
        import json

        self.declare()
        symbol = "_payload" if sys.platform == "darwin" else "payload"
        section = (
            ".section __TEXT,__const"
            if sys.platform == "darwin"
            else ".section .rodata"
        )
        for directive in ('.include "data.s"', '.incbin "data.bin"'):
            with self.subTest(directive=directive):
                tail = (
                    symbol
                    + "\n"
                    + section
                    + "\n"
                    + symbol
                    + ":\n"
                    + directive
                    + "\n.text\n"
                )
                self.put(
                    "main.cpp",
                    'asm(".globl " ' + json.dumps(tail) + ");\n"
                    'extern "C" const unsigned char payload[];\n'
                    "int main() { return payload[0]; }\n",
                )
                self.put("data.s", ".byte 7\n")
                (self.project / "data.bin").write_bytes(bytes([7]))
                self.invoke("run", expected=7)
                unchanged = self.invoke("build", "-v")
                self.assertIn("compiled=1 archived=0 linked=0", unchanged.stderr)
                self.assertIn(
                    "[uncached] Assembly input closure is not tracked", unchanged.stderr
                )
                self.put("data.s", ".byte 9\n")
                (self.project / "data.bin").write_bytes(bytes([9]))
                changed = self.invoke("run", expected=9)
                self.assertIn("compiled=1 archived=0 linked=1", changed.stderr)

    def test_backslash_source_bypasses_lossy_depfile(self):
        self.declare(r'b.executable("app").source("unit\\name.cpp");')
        source = self.put(
            r"unit\name.cpp",
            "#include <cstdio>\n#define LONG_MACRO 123456789\n"
            "#define X 123456789\n"
            'int main(){(void)LONG_MACRO;printf("%d\\n",__builtin_COLUMN());}\n',
        )
        decoy = self.put("unit/name.cpp", "// Decoy: not compiled.\n")
        self.check_backslash_inputs(source, source, decoy, "source path")

    def test_backslash_header_line_markers_bypass_lossy_depfile(self):
        self.declare()
        for kind in ("direct", "macro", "transitive"):
            with self.subTest(include=kind):
                include = '#include "unit\\name.hpp"\n'
                if kind == "macro":
                    include = '#define HEADER "unit\\name.hpp"\n#include HEADER\n'
                elif kind == "transitive":
                    self.put("wrapper.hpp", include)
                    include = '#include "wrapper.hpp"\n'
                self.put(
                    "main.cpp",
                    "#include <cstdio>\n#define LONG_MACRO 123456789\n"
                    "#define X 123456789\n"
                    + include
                    + 'int main(){printf("%d\\n",value());}\n',
                )
                header = self.put(
                    r"unit\name.hpp",
                    "inline int value(){(void)LONG_MACRO;return __builtin_COLUMN();}\n",
                )
                decoy = self.put("unit/name.hpp", "// Decoy: not included.\n")
                self.check_backslash_inputs("main.cpp", header, decoy, "line marker")

    def test_line_marker_backslash_after_escaped_quote_is_lossy(self):
        self.declare('b.executable("app").source("main.cpp").include(".");')
        self.put(
            "main.cpp",
            "#include <cstdio>\n#define LONG_MACRO 123456789\n"
            '#define X 123456789\n#include <unit"\\name.hpp>\n'
            'int main(){printf("%d\\n",value());}\n',
        )
        header = self.put(
            r'unit"\name.hpp',
            "inline int value(){(void)LONG_MACRO;return __builtin_COLUMN();}\n",
        )
        decoy = self.put('unit"/name.hpp', "// Decoy: not included.\n")
        self.check_backslash_inputs(
            "main.cpp", header, decoy, "line marker", flags=["-I."]
        )

    def test_control_header_line_markers_bypass_lossy_depfile(self):
        for label, control in (
            ("LF", "\n"),
            ("CR", "\r"),
            ("VT", "\v"),
            ("FF", "\f"),
            ("TAB", "\t"),
        ):
            with self.subTest(control=label):
                directory = "inc" + control + "name"
                # Three-digit octal is a legal C++ escape, including for VT.
                literal = "inc\\%03oname" % ord(control)
                self.declare(
                    'b.executable("app").source("main.cpp").include("' + literal + '");'
                )
                self.put(
                    "main.cpp",
                    "#include <cstdio>\n#define LONG_MACRO 123456789\n"
                    "#define X 123456789\n#include <unit.hpp>\n"
                    'int main(){printf("%d\\n",value());}\n',
                )
                original = (
                    "inline int value(){(void)LONG_MACRO;return __builtin_COLUMN();}\n"
                )
                header = self.put(directory + "/unit.hpp", original)
                decoys = [
                    self.put("inc", "// Decoy: not included.\n"),
                    self.put("name/unit.hpp", "// Decoy: not included.\n"),
                ]
                flags = ["-I" + directory]
                preprocess = [COMPILER, "-std=c++17", *flags, "-E", "main.cpp"]
                first_text = self.command(preprocess).stdout
                first = self.invoke("run", "-v")
                self.assertEqual(first.stdout, self.direct(flags=flags).stdout)
                self.assertIn("compiled=1", first.stderr)

                header.write_text(original.replace("LONG_MACRO", "X"))
                self.assertEqual(
                    first_text,
                    self.command(preprocess).stdout,
                    "Fixture must preserve preprocessed bytes",
                )
                direct = self.direct(flags=flags)
                self.assertNotEqual(first.stdout, direct.stdout)
                incremental = self.invoke("run")
                self.assertEqual(incremental.stdout, direct.stdout)
                self.assertIn("compiled=1 archived=0 linked=1", incremental.stderr)
                self.assertIn("[uncached]", first.stderr)
                self.assertIn("line marker filename", first.stderr)
                self.assertIn(
                    "compiled=1 archived=0 linked=0", self.invoke("build").stderr
                )

                for decoy in decoys:
                    decoy.unlink()
                header.write_text(original)
                without_decoys = self.invoke("run")
                self.assertEqual(without_decoys.stdout, first.stdout)
                self.assertIn("compiled=1", without_decoys.stderr)
                self.assertIn(
                    "compiled=1 archived=0 linked=0", self.invoke("build").stderr
                )

    def test_line_marker_quote_and_unicode_paths_remain_cacheable(self):
        self.declare('b.executable("app").source("main.cpp").include(".");')
        for name in ('unit"name.hpp', "单元.hpp", "plain.hpp"):
            with self.subTest(header=name):
                self.put(
                    "main.cpp",
                    "#include <cstdio>\n#define LONG_MACRO 123456789\n"
                    "#define X 123456789\n#include <" + name + ">\n"
                    'int main(){printf("%d\\n",value());}\n',
                )
                header = self.put(
                    name,
                    "inline int value(){(void)LONG_MACRO;return __builtin_COLUMN();}\n",
                )
                preprocess = [COMPILER, "-std=c++17", "-I.", "-E", "main.cpp"]
                first_text = self.command(preprocess).stdout
                first = self.invoke("run")
                self.assertEqual(first.stdout, self.direct(flags=["-I."]).stdout)
                unchanged = self.invoke("build", "-v")
                self.assertIn("compiled=0 archived=0 linked=0", unchanged.stderr)
                self.assertNotIn("[uncached]", unchanged.stderr)
                header.write_text(header.read_text().replace("LONG_MACRO", "X"))
                self.assertEqual(first_text, self.command(preprocess).stdout)
                direct = self.direct(flags=["-I."])
                self.assertNotEqual(first.stdout, direct.stdout)
                incremental = self.invoke("run")
                self.assertEqual(incremental.stdout, direct.stdout)
                self.assertIn("compiled=1 archived=0 linked=1", incremental.stderr)
                self.assertIn(
                    "compiled=0 archived=0 linked=0", self.invoke("build").stderr
                )

    def backslash_header(self):
        self.put(
            "main.cpp",
            "#include <cstdio>\n#define LONG_MACRO 123456789\n"
            "#define X 123456789\n#include <value.hpp>\n"
            'int main(){printf("%d\\n",value());}\n',
        )
        header = self.put(
            r"inc\dir/value.hpp",
            "inline int value(){(void)LONG_MACRO;return __builtin_COLUMN();}\n",
        )
        decoy = self.put("inc/dir/value.hpp", "// Decoy: not included.\n")
        return header, decoy

    def test_backslash_include_path_bypasses_lossy_depfile(self):
        self.declare(r'b.executable("app").source("main.cpp").include("inc\\dir");')
        header, decoy = self.backslash_header()
        self.check_backslash_inputs(
            "main.cpp", header, decoy, "-I", flags=[r"-Iinc\dir"]
        )

    def test_backslash_include_environment_bypasses_lossy_depfile(self):
        self.declare()
        for name in ("CPATH", "CPLUS_INCLUDE_PATH"):
            with self.subTest(variable=name):
                header, decoy = self.backslash_header()
                self.env[name] = str(header.parent)
                try:
                    self.check_backslash_inputs("main.cpp", header, decoy, name)
                finally:
                    del self.env[name]

    def test_backslash_working_directory_builds_without_decoys(self):
        parent = self.project
        self.project = parent / r"cwd\dir"
        try:
            self.put("cxxrc.toml", CONFIG)
            self.declare()
            self.put("main.cpp", '#include <cstdio>\nint main(){puts("cwd");}\n')
            result = self.invoke("run", "-v")
            self.assertEqual(result.stdout, self.direct().stdout)
            self.assertIn("working directory", result.stderr)
            self.assertIn("compiled=1 archived=0 linked=0", self.invoke("build").stderr)
        finally:
            self.project = parent

    def test_uncached_objects_allow_content_based_archive_and_link_reuse(self):
        self.declare(
            r'auto& lib=b.static_library("lib").source("unit\\name.cpp");'
            'b.executable("app").source("main.cpp").link(lib);'
        )
        source = self.put(r"unit\name.cpp", "int value(){return 1;}\n")
        self.put("main.cpp", "int value();int main(){return value();}\n")
        self.invoke("run", expected=1)
        self.assertIn("compiled=1 archived=0 linked=0", self.invoke("build").stderr)
        source.write_text("int value(){return 2;}\n")
        result = self.invoke("run", expected=2)
        self.assertIn("compiled=1 archived=1 linked=1", result.stderr)
        self.assertIn("compiled=1 archived=0 linked=0", self.invoke("build").stderr)

    def test_define_string_escapes_keep_normal_path_cacheable(self):
        self.declare(
            r'b.executable("app").source("main.cpp").define("TEXT=\"a\\\\b\"");'
        )
        self.put("main.cpp", "#include <cstdio>\nint main(){puts(TEXT);}\n")
        result = self.invoke("run")
        self.assertEqual(result.stdout, "a\\b\n")
        unchanged = self.invoke("build", "-v")
        self.assertIn("compiled=0 archived=0 linked=0", unchanged.stderr)
        self.assertNotIn("[uncached]", unchanged.stderr)

    def symlink_parent(self):
        (self.project / "real/sub").mkdir(parents=True)
        (self.project / "link").symlink_to("real/sub", target_is_directory=True)

    def test_symlink_parent_source_selection(self):
        self.declare('b.executable("app").source("link/../main.cpp");')
        self.put("main.cpp", "int main(){return 11;}\n")
        self.put("real/main.cpp", '#include "value.hpp"\nint main(){return VALUE;}\n')
        self.put("real/value.hpp", "#define VALUE 23\n")
        self.symlink_parent()
        self.direct("link/../main.cpp", expected=23)
        self.invoke("run", expected=23)

    def test_source_symlink_keeps_relative_includes(self):
        self.declare()
        self.put("real/source.cpp", '#include "value.hpp"\nint main(){return VALUE;}\n')
        self.put("real/value.hpp", "#define VALUE 11\n")
        self.put("value.hpp", "#define VALUE 23\n")
        (self.project / "main.cpp").symlink_to("real/source.cpp")
        self.direct(expected=23)
        self.invoke("run", expected=23)

    def test_raw_symlink_header_invalidates_identical_preprocessing(self):
        self.declare()
        self.put(
            "main.cpp",
            "#include <cstdio>\n#define LONG_MACRO 123456789\n"
            '#define X 123456789\n#include "link/../value.hpp"\n'
            'int main(){printf("%d\\n",value());}\n',
        )
        self.put("value.hpp", "// Decoy: not included by the compiler.\n")
        header = self.put(
            "real/value.hpp",
            "inline int value(){(void)LONG_MACRO;return __builtin_COLUMN();}\n",
        )
        self.symlink_parent()
        first_text = self.command([COMPILER, "-std=c++17", "-E", "main.cpp"]).stdout
        first = self.invoke("run")
        self.assertEqual(first.stdout, self.direct().stdout)
        self.assertIn("compiled=0", self.invoke("build").stderr)
        header.write_text(header.read_text().replace("LONG_MACRO", "X"))
        second_text = self.command([COMPILER, "-std=c++17", "-E", "main.cpp"]).stdout
        self.assertEqual(
            first_text, second_text, "Fixture must preserve preprocessed bytes"
        )
        direct = self.direct()
        self.assertNotEqual(
            first.stdout, direct.stdout, "Fixture must change the source column"
        )
        incremental = self.invoke("run")
        self.assertEqual(incremental.stdout, direct.stdout)
        self.assertIn("compiled=1", incremental.stderr)
        self.assertIn("compiled=0", self.invoke("build").stderr)

    def test_ascii_case_collision_rejected_at_declaration(self):
        self.declare(
            'b.executable("app").source("main.cpp");'
            'b.static_library("APP").source("main.cpp");'
            'throw std::runtime_error("DECLARATION_CONTINUED");'
        )
        self.put("main.cpp", "int main(){return 0;}\n")
        result = self.invoke("build", expected=None)
        self.assertIn("Duplicate target name", result.stderr)
        self.assertNotIn("DECLARATION_CONTINUED", result.stderr)
        self.assertFalse((self.project / ".cxx/build").exists())

    def test_graph_validation_uses_the_same_case_rule(self):
        # Deliberately bypass Build::add through a mutable object behind its const
        # accessor, to exercise the graph validator's independent invariant check.
        self.declare(
            'b.executable("app").source("main.cpp");'
            'auto& other=b.executable("other").source("main.cpp");'
            'const_cast<std::string&>(other.name())="APP";'
        )
        self.put("main.cpp", "int main(){return 0;}\n")
        result = self.invoke("build", expected=None)
        self.assertIn("Duplicate target name", result.stderr)
        self.assertFalse((self.project / ".cxx/build").exists())

    def test_distinct_mixed_case_names_keep_output_layout(self):
        self.declare(
            'b.executable("App").source("main.cpp");'
            'b.executable("APP_Helper").source("main.cpp");'
        )
        self.put("main.cpp", "int main(){return 0;}\n")
        self.invoke("build")
        for name in ("App", "APP_Helper"):
            self.assertTrue((self.project / ".cxx/build" / name / name).is_file())

    def tty(self, mode, expected, redirect=False):
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(self.project)
            os.execve(
                sys.executable,
                [
                    sys.executable,
                    "-c",
                    PTY_SHELL,
                    CXX,
                    mode,
                    "redirect" if redirect else "inherit",
                ],
                self.env,
            )
        transcript = bytearray()
        sent = False
        restored_input = False
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                transcript.extend(chunk)
                if re.search(rb"(?:^|\n)INPUT:\r?\n", transcript) and not sent:
                    sent = True
                    if mode == "interrupt":
                        os.write(fd, b"\x03")
                    elif mode in ("terminate", "hangup"):
                        match = re.search(rb"CXX-PID:(\d+)", transcript)
                        self.assertIsNotNone(match, transcript)
                        os.kill(
                            int(match[1]),
                            signal.SIGTERM if mode == "terminate" else signal.SIGHUP,
                        )
                    elif mode == "quit":
                        os.write(fd, b"\x1c")
                    else:
                        os.write(fd, b"hello\n")
                if b"SHELL-INPUT:" in transcript and not restored_input:
                    restored_input = True
                    os.write(fd, b"restored\n")
                if b"SHELL-READ:restored" in transcript:
                    break
            text = transcript.decode(errors="replace")
            self.assertIn("EXIT:%d FOREGROUND:1" % expected, text)
            self.assertIn("SHELL-READ:restored", text)
            if mode == "normal":
                self.assertIn("READ:hello", text)
            elif mode == "background":
                self.assertIn("BACKGROUND:1", text)
        finally:
            stop_tree(pid)
            os.waitpid(pid, 0)
            os.close(fd)

    def test_pty_input_and_foreground_restore(self):
        self.declare()
        self.put(
            "main.cpp",
            "#include <cstdio>\nint main(){char text[32];"
            'puts("INPUT:");fflush(stdout);'
            "if(!fgets(text,sizeof(text),stdin))return 9;"
            'printf("READ:%s",text);return 7;}\n',
        )
        self.invoke("build")
        self.invoke(
            "run", expected=9
        )  # Non-TTY stdin still reaches the program as EOF.
        for mode, expected in (
            ("normal", 7),
            ("interrupt", 130),
            ("terminate", 143),
            ("hangup", 129),
            ("quit", 131),
        ):
            with self.subTest(mode=mode):
                self.tty(mode, expected)

    def test_pty_redirected_stdin_uses_controlling_terminal(self):
        self.declare()
        self.put(
            "main.cpp",
            "#include <cstdio>\n#include <cstring>\n#include <unistd.h>\n"
            'int main(int argc,char** argv){FILE* tty=fopen("/dev/tty","r+");'
            "if(!tty)return 8;"
            'if(argc>1&&strcmp(argv[1],"background")==0){'
            'printf("BACKGROUND:%d\\n",tcgetpgrp(fileno(tty))!=getpgrp());return 0;}'
            "if(fgetc(stdin)!=EOF)return 10;char text[32];"
            'puts("INPUT:");fflush(stdout);'
            "if(!fgets(text,sizeof(text),tty))return 9;"
            'printf("READ:%s",text);fclose(tty);return 7;}\n',
        )
        self.invoke("build")
        for mode, expected in (
            ("normal", 7),
            ("interrupt", 130),
            ("terminate", 143),
            ("hangup", 129),
            ("quit", 131),
            ("background", 0),
        ):
            with self.subTest(mode=mode):
                self.tty(mode, expected, redirect=True)

    def test_pty_background_parent_does_not_take_terminal(self):
        self.declare()
        self.put(
            "main.cpp",
            "#include <cstdio>\n#include <unistd.h>\n"
            'int main(){printf("BACKGROUND:%d\\n",tcgetpgrp(0)!=getpgrp());}\n',
        )
        self.invoke("build")
        self.tty("background", 0)

    def test_pty_exec_error_restores_foreground(self):
        self.declare()
        self.put("main.cpp", "int main(){return 0;}\n")
        self.invoke("build")
        executable = self.project / ".cxx/build/app/app"
        # The content cache remains valid, but execv must now fail with EACCES.
        executable.chmod(executable.stat().st_mode & ~0o111)
        for redirect in (False, True):
            with self.subTest(redirect=redirect):
                self.tty("exec-error", 127, redirect=redirect)

    def test_pty_build_error_restores_foreground(self):
        self.declare('throw std::runtime_error("EXPECTED_FAILURE");')
        self.invoke("build", expected=None)
        for redirect in (False, True):
            with self.subTest(redirect=redirect):
                self.tty("error", 1, redirect=redirect)
