#!/usr/bin/env python3
"""Check compiler propagation without recursively running the build test suite."""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import pytest

pytestmark = pytest.mark.tooling

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(relative):
    spec = importlib.util.spec_from_file_location(
        pathlib.Path(relative).stem, ROOT / relative
    )
    module = importlib.util.module_from_spec(spec)
    # Avoid writing import caches into the source tree.
    exec(
        compile(spec.loader.get_source(spec.name), str(ROOT / relative), "exec"),
        module.__dict__,
    )
    return module


class Tooling(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cxx-tooling-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name).resolve()
        self.previous_cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.previous_cwd)
        for name in ("path", "override", "explicit"):
            directory = self.root / name
            directory.mkdir()
            target = directory / "clang"
            target.write_text("compiler fixture\n")
            target.chmod(0o755)
            (directory / "clang++").symlink_to(target)
        self.environment = mock.patch.dict(
            os.environ, {"PATH": str(self.root / "path")}, clear=True
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_compilation_database_uses_scan_flags_and_decodes_paths(self):
        compdb = load("scripts/compile_commands.py")
        root = self.root / 'project 中文 "quoted"'
        source = str(root / "src/main.cpp")
        output = str(root / ".cxx/build/app/.objects/main.o")
        args = [
            "/path/clang++",
            "-std=c++23",
            "-fPIC",
            "-DVALUE=hello world",
            "-isystem",
            str(root / "vendor"),
            "-x",
            "c++",
            "-E",
            "-MD",
            "-MF",
            output + ".scan.d",
            "-MT",
            "cxxdeps",
            source,
            "-o",
            output + ".scan.ii",
        ]

        def quote(value):
            return (
                '"'
                + "".join(
                    ("\\" + chr(c))
                    if c in (34, 92)
                    else ("\\%03o" % c)
                    if c < 32 or c >= 127
                    else chr(c)
                    for c in value.encode("utf-8")
                )
                + '"'
            )

        log = "[command] " + " ".join(map(quote, args)) + "\n"
        driver = log.replace(".cxx/build/app/.objects", ".cxx/driver")
        entries = compdb.entries_from_log(driver + log + log, root)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["file"], source)
        self.assertEqual(entry["output"], output)
        self.assertEqual(entry["directory"], str(root))
        self.assertEqual(entry["arguments"], args[:8] + ["-c", source, "-o", output])

    def test_compilation_database_rejects_empty_scan_log(self):
        compdb = load("scripts/compile_commands.py")
        with self.assertRaisesRegex(RuntimeError, "No project compile commands"):
            compdb.entries_from_log("[build] targets=0\n", self.root)

    def test_compilation_database_failure_preserves_existing_file(self):
        compdb = load("scripts/compile_commands.py")
        destination = self.root / "compile_commands.json"
        destination.write_text("old database")
        result = subprocess.CompletedProcess([], 1, "", "fixture build failure\n")
        with (
            mock.patch.object(compdb, "ROOT", self.root),
            mock.patch.object(sys, "argv", ["compile_commands.py"]),
            mock.patch.object(compdb.subprocess, "run", return_value=result),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "left unchanged"):
                compdb.main()
        self.assertEqual(destination.read_text(), "old database")

    def compiler_path_fixture(self):
        (self.root / "real/sub").mkdir(parents=True)
        (self.root / "link").symlink_to("real/sub", target_is_directory=True)
        for directory, identity in (
            (self.root / "real", "REQUESTED"),
            (self.root, "WRONG"),
        ):
            target = directory / "clang"
            target.write_text('#!/bin/sh\nprintf "%s\\n" "' + identity + ':$0"\n')
            target.chmod(0o755)
            (directory / "clang++").symlink_to("clang")
        return str(self.root / "link/../clang++")

    def test_build_preserves_compiler_path_semantics(self):
        selected = self.compiler_path_fixture()
        build = load("scripts/build.py")
        real_run = subprocess.run
        for choice in (selected, "link/../clang++", "clang++"):
            with self.subTest(choice=choice):
                argv = ["build.py", "--compiler", choice, "--output", "out", "--test"]

                def run(command, **kwargs):
                    if "-std=c++23" in command:
                        result = real_run(
                            command, check=True, text=True, capture_output=True
                        )
                        self.assertEqual(result.stdout, "REQUESTED:" + selected + "\n")
                        self.assertEqual(command[0], selected)
                    return subprocess.CompletedProcess(command, 0)

                with (
                    mock.patch.dict(os.environ, {"PATH": "link/.."}),
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(
                        build.subprocess, "run", side_effect=run
                    ) as calls,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    build.main()
                pytest_commands = [
                    call.args[0]
                    for call in calls.call_args_list
                    if call.args[0][:3] == [sys.executable, "-m", "pytest"]
                ]
                self.assertEqual(len(pytest_commands), 1, calls.call_args_list)
                command = pytest_commands[0]
                compiler = next(
                    arg.removeprefix("--compiler=")
                    for arg in command
                    if arg.startswith("--compiler=")
                )
                self.assertEqual(compiler, selected)
                result = real_run(
                    [compiler],
                    cwd=self.root / "real/sub",
                    check=True,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.stdout, "REQUESTED:" + selected + "\n")

    def test_pytest_resolver_preserves_compiler_path_semantics(self):
        selected = self.compiler_path_fixture()
        conftest = load("conftest.py")
        for choice in (selected, "link/../clang++", "clang++"):
            with self.subTest(choice=choice):
                with mock.patch.dict(os.environ, {"PATH": "link/.."}):
                    compiler = conftest.resolve_compiler(choice)
                result = subprocess.run(
                    [compiler],
                    cwd=self.root / "real/sub",
                    check=True,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.stdout, "REQUESTED:" + selected + "\n")
                self.assertEqual(compiler, selected)

    def test_benchmark_preserves_compiler_path_semantics(self):
        selected = self.compiler_path_fixture()
        benchmark = load("scripts/benchmark.py")
        real_run = subprocess.run
        for choice in ("absolute", "relative", "path", "override"):
            with self.subTest(choice=choice):
                argv = [
                    "benchmark.py",
                    "--cxx",
                    "cxx",
                    "--output",
                    "report.json",
                    "--units",
                    "1",
                ]
                env = {
                    "PATH": "link/..",
                    "CXX_BOOTSTRAP_COMPILER": str(self.root / "clang++"),
                }
                if choice in ("absolute", "relative"):
                    argv += [
                        "--compiler",
                        selected if choice == "absolute" else "link/../clang++",
                    ]
                elif choice == "override":
                    env["CXX_BOOTSTRAP_COMPILER"] = "link/../clang++"
                else:
                    env["CXX_BOOTSTRAP_COMPILER"] = ""

                def run(command, **kwargs):
                    if command[1] == "--version":
                        return real_run(command, **kwargs)
                    compiler = kwargs["env"]["CXX_BOOTSTRAP_COMPILER"]
                    result = real_run(
                        [compiler],
                        cwd=kwargs.get("cwd", self.root),
                        env=kwargs["env"],
                        check=True,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.stdout, "REQUESTED:" + selected + "\n")
                    self.assertEqual(compiler, selected)
                    if command[1] == "new":
                        pathlib.Path(command[2]).mkdir()
                    return subprocess.CompletedProcess(
                        command, 0, "", "compiled=1 archived=0 linked=1"
                    )

                with (
                    mock.patch.dict(os.environ, env),
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(benchmark.subprocess, "run", side_effect=run),
                    mock.patch.object(
                        benchmark.platform, "platform", return_value="test-platform"
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    benchmark.main()
                report = json.loads((self.root / "report.json").read_text())
                self.assertEqual(report["compiler_path"], selected)
                self.assertEqual(report["compiler"], "REQUESTED:" + selected + "\n")
                self.assertEqual(len(report["results"]), 6)

    def test_build_passes_selected_compiler_to_seed_and_suites(self):
        build = load("scripts/build.py")
        selected = str(self.root / "explicit/clang++")
        argv = [
            "build.py",
            "--compiler",
            "explicit/clang++",
            "--output",
            str(self.root / "out"),
            "--test",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(build.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            build.main()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][0], selected)
        self.assertEqual(
            commands[1],
            [str(self.root / "out/cxx-seed"), "build", "cxx", "support_test"],
        )
        self.assertEqual(run.call_args_list[1].kwargs["cwd"], ROOT)
        self.assertEqual(
            run.call_args_list[1].kwargs["env"]["CXX_BOOTSTRAP_COMPILER"], selected
        )
        self.assertEqual(run.call_args_list[1].kwargs["env"]["CXX_SDK_ROOT"], str(ROOT))
        for name in ("cxx", "support_test"):
            entry = self.root / "out" / name
            self.assertTrue(entry.is_symlink())
            self.assertEqual(
                os.readlink(entry),
                os.path.relpath(ROOT / ".cxx/build" / name / name, entry.parent),
            )
        self.assertNotIn(str(ROOT / "tests/support_test.cpp"), commands[0])
        self.assertEqual(
            commands[2:],
            [
                [str(self.root / "out/support_test")],
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(ROOT / "tests"),
                    "--cxx=" + str(self.root / "out/cxx"),
                    "--compiler=" + selected,
                    "--junitxml",
                    str(self.root / "out/pytest-results.xml"),
                ],
            ],
        )
        self.assertEqual(
            run.call_args_list[2].kwargs["env"]["CXX_BOOTSTRAP_COMPILER"], selected
        )
        self.assertTrue(run.call_args_list[2].kwargs["check"])
        self.assertEqual(run.call_args_list[3].kwargs["cwd"], ROOT)
        self.assertTrue(run.call_args_list[3].kwargs["check"])

    def test_task_unit_passes_compiler_to_build_and_execution(self):
        taskfile = (ROOT / "Taskfile.yml").read_text()
        unit = taskfile.split("  unit:\n", 1)[1].split("\n  bench:", 1)[0]
        commands = [line.strip() for line in unit.splitlines() if "- " in line]
        self.assertEqual(
            commands,
            [
                "- CXX_BOOTSTRAP_COMPILER={{.COMPILER | shellQuote}} ./build/cxx build support_test",
                "- CXX_BOOTSTRAP_COMPILER={{.COMPILER | shellQuote}} ./build/support_test",
            ],
        )

    def test_failed_self_build_does_not_link_or_test(self):
        build = load("scripts/build.py")
        argv = ["build.py", "--output", str(self.root / "out"), "--test"]
        failure = subprocess.CalledProcessError(1, "cxx-seed")
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                build.subprocess,
                "run",
                side_effect=[subprocess.CompletedProcess([], 0), failure],
            ) as run,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                build.main()
        self.assertEqual(run.call_count, 2)
        self.assertFalse((self.root / "out/cxx").is_symlink())
        self.assertFalse((self.root / "out/support_test").is_symlink())

    def test_relative_entry_tracks_replaced_artifact(self):
        build = load("scripts/build.py")
        artifact = self.root / "artifact"
        artifact.write_text("first")
        entry = self.root / "cxx"
        entry.write_text("old copy")
        build.link_artifact(artifact, entry)
        self.assertEqual(os.readlink(entry), "artifact")
        replacement = self.root / "replacement"
        replacement.write_text("second")
        replacement.replace(artifact)
        self.assertEqual(entry.read_text(), "second")
        build.link_artifact(artifact, entry)
        self.assertEqual(entry.read_text(), "second")
        build.link_artifact(artifact, artifact)
        self.assertFalse(artifact.is_symlink())
        self.assertEqual(artifact.read_text(), "second")

    def test_benchmark_execution_and_report_use_same_compiler(self):
        benchmark = load("scripts/benchmark.py")
        for choice in ("path", "override", "explicit"):
            with self.subTest(choice=choice):
                selected = str(self.root / choice / "clang++")
                argv = [
                    "benchmark.py",
                    "--cxx",
                    "cxx",
                    "--output",
                    "report.json",
                    "--units",
                    "1",
                ]
                env = (
                    {}
                    if choice == "path"
                    else {"CXX_BOOTSTRAP_COMPILER": "override/clang++"}
                )
                if choice == "explicit":
                    argv += ["--compiler", "explicit/clang++"]
                calls = []

                def run(command, **kwargs):
                    calls.append(command)
                    self.assertEqual(kwargs["env"]["CXX_BOOTSTRAP_COMPILER"], selected)
                    if command[1] == "new":
                        pathlib.Path(command[2]).mkdir()
                    return subprocess.CompletedProcess(
                        command, 0, "", "compiled=1 archived=0 linked=1"
                    )

                def version(command, **kwargs):
                    self.assertEqual(command, [selected, "--version"])
                    self.assertEqual(kwargs["env"]["CXX_BOOTSTRAP_COMPILER"], selected)
                    return "Fixture version: " + selected

                with (
                    mock.patch.dict(os.environ, env),
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(benchmark.subprocess, "run", side_effect=run),
                    mock.patch.object(
                        benchmark.subprocess, "check_output", side_effect=version
                    ),
                    mock.patch.object(
                        benchmark.platform, "platform", return_value="test-platform"
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    benchmark.main()
                report = json.loads((self.root / "report.json").read_text())
                self.assertEqual(report["compiler_path"], selected)
                self.assertEqual(report["compiler"], "Fixture version: " + selected)
                self.assertEqual(sum(command[1] == "build" for command in calls), 6)
                self.assertEqual(len(report["results"]), 6)

    def test_benchmark_version_failure_does_not_write_report(self):
        benchmark = load("scripts/benchmark.py")
        argv = ["benchmark.py", "--cxx", "cxx", "--output", "report.json"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                benchmark.subprocess,
                "check_output",
                side_effect=subprocess.CalledProcessError(1, "compiler"),
            ),
            mock.patch.object(benchmark.subprocess, "run") as run,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                benchmark.main()
        run.assert_not_called()
        self.assertFalse((self.root / "report.json").exists())

    def test_integration_environment_is_reset_per_test(self):
        integration = load("tests/integration.py")
        integration.COMPILER = str(self.root / "explicit/clang++")
        with mock.patch.dict(
            os.environ,
            {"CXX_BOOTSTRAP_COMPILER": "wrong", "CXX_BOOTSTRAP_ARCHIVER": "wrong"},
        ):
            integration.Integration.setUpClass()
            try:
                first = integration.Integration()
                first.setUp()
                self.assertEqual(
                    first.env["CXX_BOOTSTRAP_COMPILER"], integration.COMPILER
                )
                self.assertNotIn("CXX_BOOTSTRAP_ARCHIVER", first.env)
                first.env.pop("CXX_BOOTSTRAP_COMPILER")
                first.env["TEST_LEAK"] = "bad"
                second = integration.Integration()
                second.setUp()
                self.assertEqual(
                    second.env["CXX_BOOTSTRAP_COMPILER"], integration.COMPILER
                )
                self.assertNotIn("TEST_LEAK", second.env)
            finally:
                integration.Integration.tearDownClass()

    def pytest_run(self, *arguments, cwd=None):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                "--color=no",
                *map(str, arguments),
            ],
            cwd=cwd,
            env=dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"),
            text=True,
            capture_output=True,
            timeout=30,
        )

    def pytest_fixture_suite(self, source):
        tests = self.root / "tests"
        tests.mkdir()
        (self.root / "conftest.py").write_text((ROOT / "conftest.py").read_text())
        (self.root / "pytest.ini").write_text((ROOT / "pytest.ini").read_text())
        suite = tests / "fixture_test.py"
        suite.write_text(source)
        return suite

    @mock.patch("os.defpath", "")
    def test_pytest_collection_and_tooling_need_no_executables(self):
        taskfile = (ROOT / "Taskfile.yml").read_text()
        self.assertIn(
            "python3 -m pytest --cxx={{.ROOT_DIR | shellQuote}}/build/cxx "
            "--compiler={{.COMPILER | shellQuote}} {{.CLI_ARGS}}",
            taskfile,
        )
        checkout = self.root / "clean checkout"
        checkout.mkdir()
        for name in ("conftest.py", "pytest.ini"):
            shutil.copyfile(ROOT / name, checkout / name)
        for name in ("tests", "scripts"):
            shutil.copytree(
                ROOT / name,
                checkout / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        self.assertFalse((checkout / "build").exists())
        self.assertFalse((checkout / ".cxx").exists())
        missing = str(self.root / "missing-executable")
        self.assertEqual(os.defpath, "")
        self.assertIsNone(shutil.which("clang++", path=os.defpath))
        external_compiler = self.root / "external compiler"
        fail_if_invoked = (
            "#!/bin/sh\n"
            'echo "Discovery must not execute tools" >&2\n'
            ': > "$0.invoked"\n'
            "exit 97\n"
        )
        external_compiler.write_text(fail_if_invoked)
        external_compiler.chmod(0o755)
        foreign = self.root / "foreign checkout"
        foreign.mkdir()
        (foreign / "conftest.py").write_text(
            "raise AssertionError('Foreign conftest loaded')\n"
        )
        executable = foreign / "cxx"
        executable.write_text(fail_if_invoked)
        executable.chmod(0o755)
        for cxx, compiler in (
            (missing, missing),
            (missing, str(external_compiler)),
            (str(executable), str(external_compiler)),
        ):
            for selection in (
                ["--collect-only"],
                ["-m", "tooling", "-k", "resolver_preserves_compiler_path_semantics"],
            ):
                with self.subTest(cxx=cxx, compiler=compiler, selection=selection):
                    result = self.pytest_run(
                        *selection,
                        "--cxx=" + cxx,
                        "--compiler=" + compiler,
                        cwd=checkout,
                    )
                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode, 0, output)
                    self.assertNotIn("Foreign conftest loaded", output)
                    self.assertNotIn("Discovery must not execute tools", output)
                    for tool in (external_compiler, executable):
                        self.assertFalse(pathlib.Path(str(tool) + ".invoked").exists())
                    if "--collect-only" in selection:
                        self.assertIn("prototypes/p02/run.py::Prototype::", output)
                        self.assertIn("self_host.py::SelfHost::", output)
                        self.assertIn("tests collected", output)
                    else:
                        self.assertIn("1 passed", output)
        self.assertFalse((checkout / "build").exists())
        self.assertFalse((checkout / ".cxx").exists())

    def test_pytest_sets_options_before_unittest_class_setup(self):
        selected = self.compiler_path_fixture()
        executable = self.root / "build/cxx"
        executable.parent.mkdir()
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        suite = self.pytest_fixture_suite(
            "import os, subprocess, unittest\n"
            "import pytest\n"
            "pytestmark = pytest.mark.e2e\n"
            "class FixtureOrder(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            f"        assert CXX == {str(executable)!r}\n"
            f"        assert COMPILER == {selected!r}\n"
            f"        os.chdir({str(self.root / 'real/sub')!r})\n"
            "        cls.output = subprocess.check_output([COMPILER], text=True, timeout=5)\n"
            "    def test_compiler(self):\n"
            f"        self.assertEqual(self.output, {'REQUESTED:' + selected + chr(10)!r})\n"
        )
        for options, environment in (
            ([], {}),
            ([], {"CXX_BOOTSTRAP_COMPILER": "link/../clang++"}),
            (
                ["--cxx=build/cxx", "--compiler=clang++"],
                {"CXX_BOOTSTRAP_COMPILER": str(self.root / "clang++")},
            ),
            (
                ["--cxx=build/cxx", "--compiler=link/../clang++"],
                {"CXX_BOOTSTRAP_COMPILER": str(self.root / "clang++")},
            ),
        ):
            with (
                self.subTest(options=options, environment=environment),
                mock.patch.dict(os.environ, {"PATH": "link/..", **environment}),
            ):
                result = self.pytest_run(suite, *options)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("1 passed", result.stdout)

    def test_pytest_rejects_invalid_executables_at_e2e_setup(self):
        suite = self.pytest_fixture_suite(
            "import unittest\n"
            "import pytest\n"
            "pytestmark = pytest.mark.e2e\n"
            "class InvalidSetup(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        raise AssertionError('Class setup must wait for executable validation')\n"
            "    def test_unreachable(self):\n"
            "        self.fail('Test must wait for executable validation')\n"
        )
        missing = self.root / "missing"
        nonexecutable = self.root / "nonexecutable"
        nonexecutable.write_text("fixture\n")
        nonexecutable.chmod(0o644)
        executable = self.root / "executable"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        for cxx, compiler, message in (
            (missing, "clang++", "CXX executable not found or not executable: "),
            (nonexecutable, "clang++", "CXX executable not found or not executable: "),
            (self.root, "clang++", "CXX executable not found or not executable: "),
            (executable, str(missing), "Compiler not found: "),
        ):
            with self.subTest(cxx=cxx, compiler=compiler):
                result = self.pytest_run(
                    suite, "--cxx=" + str(cxx), "--compiler=" + compiler
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn(
                    message + str(missing if cxx == executable else cxx), output
                )
                self.assertIn("1 error", output)
                self.assertNotIn("AssertionError", output)
