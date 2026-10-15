#!/usr/bin/env python3
"""Build CXX with CXX, then rebuild and exercise the resulting launcher."""
import argparse
import os
import pathlib
import re
import runpy
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class SelfHost(unittest.TestCase):
    def test_self_build_rebuild_and_incremental(self):
        with tempfile.TemporaryDirectory(prefix="cxx-self-host-") as temporary:
            root = pathlib.Path(temporary) / 'project 中文 "quoted"'
            root.mkdir()
            for name in ("src", "include", "third_party"):
                shutil.copytree(ROOT / name, root / name)
            for name in ("build.cpp", "cxxrc.toml", "tests/support_test.cpp"):
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, destination)
            env = dict(os.environ, CXX_BOOTSTRAP_COMPILER=COMPILER, CXX_SDK_ROOT=str(root))

            def invoke(executable, *arguments):
                if arguments[0] == "build" and "-v" not in arguments:
                    arguments = (*arguments, "-v")
                result = subprocess.run([str(executable), *arguments], cwd=root, env=env,
                                        text=True, capture_output=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result

            def check_counts(result, expected):
                match = re.search(r"compiled=(\d+) archived=(\d+) linked=(\d+)", result.stderr)
                self.assertIsNotNone(match, result.stderr)
                self.assertEqual(tuple(map(int, match.groups())), expected, result.stdout + result.stderr)

            check_counts(invoke(CXX, "build"), (4, 1, 2))
            artifact = root / ".cxx/build/cxx/cxx"
            (root / "build").mkdir()
            current = root / "build/cxx"
            runpy.run_path(str(ROOT / "scripts/build.py"))["link_artifact"](artifact, current)
            self.assertEqual(os.readlink(current), "../.cxx/build/cxx/cxx")
            self.assertTrue((root / ".cxx/build/support_test/support_test").is_file())

            # Exercise the SDK path embedded by build.cpp, including quotes and UTF-8.
            # Removing the override also changes the action environment, so this is
            # a real self-rebuild rather than merely a cache lookup.
            env.pop("CXX_SDK_ROOT")
            rebuilt = invoke(current, "build", "-v")
            check_counts(rebuilt, (4, 1, 2))
            commands = [shlex.split(line[len("[command] "):]) for line in rebuilt.stderr.splitlines()
                        if line.startswith("[command] ")]
            target_compiles = [cmd for cmd in commands if "-c" in cmd
                               and ".cxx/build/" in cmd[cmd.index("-o") + 1]]
            self.assertEqual(len(target_compiles), 4)
            for command in target_compiles:
                for flag in ("-std=c++23", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-isystem"):
                    self.assertIn(flag, command)
            check_counts(invoke(current, "build"), (0, 0, 0))

            source = root / "src/main.cpp"
            text = source.read_text()
            entry = "\nint main(int argc, char** argv) {"
            self.assertEqual(text.count(entry), 1)
            source.write_text(text.replace(entry, entry + '''
    if (argc == 2 && std::string(argv[1]) == "--self-host-probe") {
        std::cout << "self-host-probe\\n";
        return 0;
    }
'''))
            check_counts(invoke(current, "build"), (1, 0, 1))
            self.assertTrue(current.is_symlink())
            self.assertEqual(invoke(current, "--self-host-probe").stdout, "self-host-probe\n")
            check_counts(invoke(current, "build"), (0, 0, 0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", required=True, type=pathlib.Path)
    parser.add_argument("--compiler", default="clang++")
    args, rest = parser.parse_known_args()
    compiler = shutil.which(args.compiler)
    if not compiler:
        parser.error("Compiler not found: " + args.compiler)
    COMPILER = str(pathlib.Path(compiler).absolute())
    CXX = args.cxx.resolve()
    unittest.main(argv=[__file__, *rest], verbosity=2)
