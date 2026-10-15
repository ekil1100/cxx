#!/usr/bin/env python3
"""Check compiler propagation without recursively running the build test suite."""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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
                for suite in ("self_host.py", "integration.py", "regressions.py"):
                    command = next(
                        call.args[0]
                        for call in calls.call_args_list
                        if str(ROOT / "tests" / suite) in call.args[0]
                    )
                    compiler = command[command.index("--compiler") + 1]
                    self.assertEqual(compiler, selected)
                    result = real_run(
                        [compiler],
                        cwd=self.root / "real/sub",
                        check=True,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.stdout, "REQUESTED:" + selected + "\n")

    def test_suite_entrypoints_preserve_compiler_path_semantics(self):
        selected = self.compiler_path_fixture()
        for suite in ("self_host.py", "integration.py", "regressions.py"):
            for choice in (selected, "link/../clang++", "clang++"):
                with self.subTest(suite=suite, choice=choice):
                    argv = [suite, "--cxx", "cxx", "--compiler", choice]
                    with (
                        mock.patch.dict(os.environ, {"PATH": "link/.."}),
                        mock.patch.object(sys, "argv", argv),
                        mock.patch.object(unittest, "main"),
                    ):
                        namespace = runpy.run_path(
                            str(ROOT / "tests" / suite), run_name="__main__"
                        )
                    compiler = namespace["COMPILER"]
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
        for suite in ("self_host.py", "integration.py", "regressions.py"):
            command = next(
                command
                for command in commands
                if str(ROOT / "tests" / suite) in command
            )
            self.assertEqual(command[command.index("--compiler") + 1], selected)
            self.assertEqual(
                command[command.index("--cxx") + 1], str(self.root / "out/cxx")
            )
        self.assertIn([sys.executable, str(ROOT / "tests/tooling_test.py")], commands)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
