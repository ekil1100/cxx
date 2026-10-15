#!/usr/bin/env python3
"""Compile and run the isolated P02 declaration/lock experiment with a selected Clang."""
import argparse
import hashlib
import json
import os
import pathlib
import platform
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BINARY = None
WORK = None
TIMEOUT = 60


def command(argv, **kwargs):
    argv = [str(arg) for arg in argv]
    print("[command] " + shlex.join(argv), flush=True)
    return subprocess.run(argv, text=True, capture_output=True, timeout=TIMEOUT, **kwargs)


def checked(argv):
    result = command(argv)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr, flush=True)
    result.check_returncode()
    return result


class Prototype(unittest.TestCase):
    def setUp(self):
        self.root = WORK / self._testMethodName
        self.root.mkdir()
        self.lock = self.root / "cxx.lock"
        self.registry = self.root / "registry.toml"
        self.registry.write_bytes((HERE / "registry.toml").read_bytes())
        self.children = []

    def tearDown(self):
        for process in self.children:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=TIMEOUT)

    def args(self, domain=("fixture-a", "debug", "target"), variant="base", mode="build", pause="none"):
        return [BINARY, self.lock, self.registry, *domain, variant, mode, pause]

    def invoke(self, expected=None, **kwargs):
        result = command(self.args(**kwargs))
        if expected:
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(expected, result.stderr)
            print("[expected rejection] " + expected, flush=True)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("declaration calls=1 handles=stable metadata_reads=0 builder=destroyed", result.stdout)
            self.assertIn("resolved calls=1", result.stdout)
            print(result.stdout, end="", flush=True)
        return result

    def inspect(self, path=None):
        result = command([BINARY, "inspect", path or self.lock, self.registry])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def sets(self):
        return {tuple(item["domain"][key] for key in ("platform", "profile", "role")): item
                for item in self.inspect()["resolution_sets"].values()}

    def add_releases(self):
        original = self.registry.read_text()
        # Same metadata, with genuinely newer direct and transitive versions available.
        newer = "".join("[[packages]]" + block.replace('version = "1.0.0"', 'version = "1.1.0"')
                        for block in original.split("[[packages]]")[1:]
                        if '\nname = "widget"\n' in block or '\nname = "core"\n' in block)
        self.registry.write_text(original + "\n" + newer)

    def start_paused(self, **kwargs):
        args = [str(arg) for arg in self.args(**kwargs)]
        print("[command] " + shlex.join(args), flush=True)
        process = subprocess.Popen(args, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        self.children.append(process)
        output = b""
        expected = ("checkpoint " + kwargs["pause"] + "\n").encode()
        deadline = time.monotonic() + TIMEOUT
        while expected not in output:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, "Checkpoint timed out: " + output.decode())
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            self.assertTrue(ready, "Checkpoint timed out: " + output.decode())
            block = os.read(process.stdout.fileno(), 4096)
            self.assertTrue(block, "Writer exited before checkpoint: " + output.decode())
            output += block
        self.assertIn(b"declaration calls=1", output)
        print(output.decode(), end="", flush=True)
        return process

    def kill_writer(self, process):
        print("[signal] SIGKILL pid=" + str(process.pid), flush=True)
        process.kill()
        process.communicate(timeout=TIMEOUT)
        self.assertEqual(process.returncode, -signal.SIGKILL)

    def snapshot(self):
        # Includes guard files, orphan stages and mtimes: locked paths must be read-only.
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def test_deferred_resolution_and_old_choices(self):
        self.invoke()
        initial = self.sets()[("fixture-a", "debug", "target")]
        self.assertEqual(set(initial["packages"]), {"widget", "core", "debug-helper"})
        self.assertEqual(len(initial["declaration"]["roots"]), 1)
        self.assertEqual(initial["declaration"]["links"], [
            {"target": "app", "package": "widget", "export": "widget::widget"},
            {"target": "second-app", "package": "widget", "export": "widget::widget"}])
        # A separate empty project produces the same canonical lock bytes.
        repeat = self.args()
        repeat[1] = self.root / "repeat" / "cxx.lock"
        checked(repeat)
        self.assertEqual(repeat[1].read_bytes(), self.lock.read_bytes())
        self.add_releases()
        snapshot = self.snapshot()
        self.invoke()
        self.assertEqual(self.snapshot(), snapshot)
        self.invoke(variant="added")
        updated = self.sets()[("fixture-a", "debug", "target")]
        for name, value in initial["packages"].items():
            self.assertEqual(updated["packages"][name], value)
        self.assertEqual(updated["packages"]["extra"]["dependencies"], {"core": 1})
        self.assertNotEqual(initial["declaration_fingerprint"], updated["declaration_fingerprint"])
        self.assertNotIn(str(self.root), self.lock.read_text())
        self.assertNotIn("index", self.lock.read_text())

    def test_configuration_domains(self):
        self.invoke()
        original = self.sets()
        self.add_releases()
        for domain in (("fixture-a", "release", "target"), ("fixture-a", "debug", "host"),
                       ("fixture-b", "debug", "target")):
            self.invoke(domain=domain)
        sets = self.sets()
        self.assertEqual(len(sets), 4)
        self.assertEqual(sets[("fixture-a", "debug", "target")], next(iter(original.values())))
        release = sets[("fixture-a", "release", "target")]["packages"]
        self.assertEqual(set(release), {"widget", "core"})
        self.assertEqual(release["widget"]["version"], "1.1.0")
        self.assertEqual(release["core"]["version"], "1.1.0")
        self.assertIn("host-helper", sets[("fixture-a", "debug", "host")]["packages"])
        self.assertIn("platform-helper", sets[("fixture-b", "debug", "target")]["packages"])
        for value in sets.values():
            self.assertEqual(value["toolchain"]["source"], "fixture:toolchain")

    def test_locked_is_read_only(self):
        snapshot = self.snapshot()
        self.invoke(mode="locked", expected="requires an existing lock")
        self.assertEqual(snapshot, self.snapshot())
        self.invoke()
        self.add_releases()
        snapshot = self.snapshot()
        self.invoke(mode="locked")
        self.assertEqual(snapshot, self.snapshot())
        self.invoke(mode="locked", variant="added", expected="declaration mismatch")
        self.assertEqual(snapshot, self.snapshot())
        self.invoke(mode="locked", domain=("fixture-a", "release", "target"), expected="missing the requested domain")
        self.assertEqual(snapshot, self.snapshot())

    def test_failures_preserve_lock(self):
        self.invoke()
        original = self.lock.read_bytes()
        for variant, message in (("incompatible", "requires explicit update"),
                                 ("bad-export", "Missing package export"),
                                 ("script-fail", "Fixture declaration failed")):
            self.invoke(variant=variant, expected=message)
            self.assertEqual(self.lock.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.candidate.*")), [])

    def test_metadata_identity_and_invalid_lock(self):
        self.invoke()
        original = self.lock.read_bytes()
        self.registry.write_text(self.registry.read_text().replace('exports = ["core::core"]',
                                                                  'exports = ["core::changed"]'))
        self.invoke(expected="Locked metadata identity changed")
        self.assertEqual(self.lock.read_bytes(), original)
        self.registry.write_bytes((HERE / "registry.toml").read_bytes())
        for invalid, message in ((original.replace(b"lock_format = 1", b"lock_format = 99"), "Unknown prototype lock format"),
                                 (b"lock_format = [", "Error while parsing"),
                                 (original.replace(b'major = 1', b'major = 2', 1), "requires explicit update")):
            self.lock.write_bytes(invalid)
            self.invoke(expected=message)
            self.assertEqual(self.lock.read_bytes(), invalid)

    def test_unresolved_metadata_preserves_lock(self):
        self.invoke()
        original = self.lock.read_bytes()
        fixture = self.registry.read_text()
        # Failure is on a newly requested package; all previously locked metadata stays intact.
        for dependency, expected in (("absent", "Missing compatible metadata"),
                                     ("extra", "Metadata cycle")):
            self.registry.write_text(fixture.replace('dependencies = [{ name = "core", major = 1 }]',
                                                     'dependencies = [{ name = "' + dependency + '", major = 1 }]'))
            self.invoke(variant="added", expected=expected)
            self.assertEqual(self.lock.read_bytes(), original)

    def test_competing_writers_retry_fresh_snapshot(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if self.lock.exists():
                    self.lock.unlink()
                if existing:
                    self.invoke(domain=("fixture-a", "release", "target"))
                old = self.lock.read_bytes() if existing else None
                first = self.start_paused(pause="before-publish")
                second_domain = ("fixture-b", "release", "host")
                self.invoke(domain=second_domain, expected="Another cxx command holds")
                if existing:
                    self.assertEqual(self.lock.read_bytes(), old)
                    self.inspect()
                else:
                    self.assertFalse(self.lock.exists())
                output, _ = first.communicate("continue\n", timeout=TIMEOUT)
                self.assertEqual(first.returncode, 0, output)
                after_first = self.sets()
                self.invoke(domain=second_domain)
                after_second = self.sets()
                self.assertEqual(len(after_second), len(after_first) + 1)
                for key, value in after_first.items():
                    self.assertEqual(after_second[key], value)

    def test_interruption_before_and_after_publication(self):
        for existing in (False, True):
            for phase in ("before-publish", "after-publish"):
                with self.subTest(existing=existing, phase=phase):
                    if self.lock.exists():
                        self.lock.unlink()
                    if existing:
                        self.invoke()
                    old = self.lock.read_bytes() if existing else None
                    writer = self.start_paused(variant="added", pause=phase)
                    if phase == "before-publish":
                        # A complete, independently parsed candidate exists, but is not published.
                        self.inspect(path=str(self.lock) + ".candidate." + str(writer.pid))
                    self.kill_writer(writer)
                    if phase == "before-publish":
                        self.assertEqual(self.lock.read_bytes() if self.lock.exists() else None, old)
                        if existing:
                            self.inspect()
                    else:
                        self.assertIn("extra", next(iter(self.sets().values()))["packages"])
                        published = self.snapshot()
                        self.invoke(variant="added", mode="locked")
                        self.assertEqual(self.snapshot(), published)
                    # The OS released the advisory lock; orphan candidates are ignored.
                    self.invoke(variant="added")
                    self.assertIn("extra", next(iter(self.sets().values()))["packages"])


def main():
    global BINARY, WORK
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default=os.environ.get("CXX_BOOTSTRAP_COMPILER", "clang++"))
    args = parser.parse_args()
    if sys.platform not in ("darwin", "linux"):
        parser.error("This prototype uses the existing Linux/macOS POSIX support")
    compiler = shutil.which(args.compiler)
    if not compiler:
        parser.error("Compiler not found: " + args.compiler)
    # Match existing runners: absolute(), not resolve(), preserves symlink/.. semantics.
    compiler = str(pathlib.Path(compiler).absolute())
    print("[platform] " + platform.platform(), flush=True)
    print("[python] " + sys.version.replace("\n", " "), flush=True)
    checked([compiler, "--version"])
    if sys.platform == "darwin":
        checked(["sw_vers"])
    manifest = json.loads((ROOT / "third_party/manifest.json").read_text())
    for dep in manifest["dependencies"]:
        for name, digest in dep["files"].items():
            if hashlib.sha256((ROOT / "third_party" / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("Vendored dependency checksum mismatch: " + name)
    with tempfile.TemporaryDirectory(prefix="cxx p02 ") as temporary:
        WORK = pathlib.Path(temporary)
        BINARY = WORK / "p02"
        common = [compiler, "-O2", "-Wall", "-Wextra", "-Wpedantic"]
        public = WORK / "declaration.o"
        checked(common + ["-std=c++17", "-pedantic-errors", "-c", HERE / "declaration.cpp", "-o", public])
        checked(common + ["-std=c++23", "-I" + str(ROOT / "src"),
                          "-isystem", ROOT / "third_party/picosha2",
                          "-isystem", ROOT / "third_party/tomlplusplus",
                          HERE / "prototype.cpp", ROOT / "src/support.cpp", public, "-o", BINARY])
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Prototype)
        result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
        if not result.wasSuccessful():
            return 1
        print("P02 PASS: 8 tests, including competing writers and 4 publication interruption cases", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.SubprocessError) as error:
        print("P02 failed: " + str(error), file=sys.stderr)
        sys.exit(1)
