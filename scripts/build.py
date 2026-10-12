#!/usr/bin/env python3
"""Build the M0 launcher and support tests without a package manager."""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "build")
    parser.add_argument("--test", action="store_true", help="Run unit and integration tests")
    args = parser.parse_args()
    compiler = shutil.which(args.compiler)
    if not compiler:
        parser.error("Compiler not found: " + args.compiler)
    compiler = str(pathlib.Path(compiler).absolute())
    if sys.platform not in ("darwin", "linux"):
        parser.error("M0 supports Linux and macOS; Windows is planned for M1")
    manifest = json.loads((ROOT / "third_party/manifest.json").read_text())
    for dependency in manifest["dependencies"]:
        for name, expected in dependency["files"].items():
            actual = hashlib.sha256((ROOT / "third_party" / name).read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError("Vendored dependency checksum mismatch: " + name)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    flags = [compiler, "-std=c++23", "-O2", "-Wall", "-Wextra", "-Wpedantic",
             "-I" + str(ROOT / "include"), "-I" + str(ROOT / "src"),
             "-isystem", str(ROOT / "third_party/picosha2"),
             "-isystem", str(ROOT / "third_party/tomlplusplus")]
    subprocess.run(flags + ["-DCXX_SDK_ROOT=" + json.dumps(str(ROOT), ensure_ascii=False),
                           str(ROOT / "src/main.cpp"), str(ROOT / "src/support.cpp"),
                           "-o", str(output / "cxx")], check=True)
    subprocess.run(flags + [str(ROOT / "tests/support_test.cpp"), str(ROOT / "src/support.cpp"),
                           str(ROOT / "src/runtime.cpp"), "-o", str(output / "support_test")], check=True)
    print("Built " + str(output / "cxx"), flush=True)
    if args.test:
        subprocess.run([str(output / "support_test")], check=True)
        for suite in ("integration.py", "regressions.py"):
            subprocess.run([sys.executable, str(ROOT / "tests" / suite),
                            "--cxx", str(output / "cxx"), "--compiler", compiler], check=True)
        subprocess.run([sys.executable, str(ROOT / "tests/tooling_test.py")], check=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print("Build failed: " + str(error), file=sys.stderr)
        sys.exit(1)
