#!/usr/bin/env python3
"""Probe explicit Clang named-module commands, independently of production CXX."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def tool(name):
    found = shutil.which(str(name))
    require(found, "Required executable not found: " + str(name))
    # Keep clang++ spelling: resolving its symlink to clang changes driver mode.
    return str(Path(found).absolute())


def default_compiler():
    override = os.environ.get("CXX_BOOTSTRAP_COMPILER")
    if override:
        return override
    for prefix in ("/opt/homebrew/opt/llvm@20", "/opt/homebrew/opt/llvm",
                   "/opt/homebrew/opt/llvm@21", "/usr/lib/llvm-20"):
        candidate = Path(prefix) / "bin/clang++"
        if candidate.is_file():
            return str(candidate)
    return "clang++"


def validate_scan(data, directory):
    """Check this fixture's P1689 contract, not a general-purpose graph parser."""
    require(data.get("version") == 1, "Expected P1689 version 1")
    expected = {
        "value.o": ({"p04:value"}, set(), "value.cppm"),
        "api.o": ({"p04"}, {"p04:value"}, "api.cppm"),
        "main.o": (set(), {"p04"}, "main.cpp"),
    }
    rules = data.get("rules", [])
    require(len(rules) == 3, "Expected exactly three translation-unit rules")
    seen = set()
    for rule in rules:
        output = Path(rule.get("primary-output", ""))
        require(output.parent == directory and output.name in expected,
                "Unexpected scan output: " + str(output))
        require(output.name not in seen, "Duplicate scan output: " + output.name)
        seen.add(output.name)
        provides, requires, source = expected[output.name]
        for key, names in (("provides", provides), ("requires", requires)):
            entries = rule.get(key, [])
            require(len(entries) == len(names)
                    and {entry.get("logical-name") for entry in entries} == names,
                    "Incorrect {} for {}: {}".format(key, output.name, entries))
        for entry in rule.get("provides", []):
            require(entry.get("is-interface") is True,
                    "Expected interface provider: " + str(entry))
            require(entry.get("source-path") == str(directory / source),
                    "Incorrect provider source: " + str(entry))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(args, work, report):
    def invoke(command, expect_failure=False):
        command = [str(arg) for arg in command]
        print("[command] " + shlex.join(command), flush=True)
        result = subprocess.run(command, cwd=work, text=True, capture_output=True,
                                timeout=120)
        report["commands"].append({"argv": command, "cwd": str(work),
                                   "returncode": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr})
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        require((result.returncode != 0) if expect_failure else (result.returncode == 0),
                "Unexpected exit {}: {}".format(result.returncode, shlex.join(command)))
        return result

    compiler = tool(args.compiler)
    scanner = tool(args.scanner or str(Path(compiler).parent / "clang-scan-deps"))
    report["compiler"] = compiler
    report["scanner"] = scanner
    compiler_version = invoke([compiler, "--version"]).stdout
    scanner_version = invoke([scanner, "--version"]).stdout
    cv = re.search(r"clang version (\d+\.\d+\.\d+)", compiler_version)
    sv = re.search(r"LLVM version (\d+\.\d+\.\d+)", scanner_version)
    require(cv and sv and cv.group(1) == sv.group(1),
            "Require Clang and clang-scan-deps with matching release versions; "
            "select a paired LLVM installation using --compiler and --scanner")
    report["tool_versions"] = [compiler_version, scanner_version]
    if sys.platform == "darwin":
        report["macos"] = invoke(["sw_vers"]).stdout
        report["sdk"] = invoke(["xcrun", "--show-sdk-path"]).stdout.strip()
    base = [compiler, "-std=c++20", "-O0", "-Wall", "-Wextra", "-Wpedantic"]
    for source in (ROOT / "fixtures").iterdir():
        shutil.copy2(source, work / source.name)
    database = []
    for source, output in (("value.cppm", "value.o"), ("api.cppm", "api.o"),
                           ("main.cpp", "main.o")):
        database.append({"directory": str(work), "file": str(work / source),
                         "output": str(work / output),
                         "arguments": base + ["-c", str(work / source),
                                              "-o", str(work / output)]})
    (work / "compile_commands.json").write_text(json.dumps(database, indent=2) + "\n")
    scan_command = [scanner, "-format=p1689",
                    "-compilation-database=" + str(work / "compile_commands.json")]
    # Deliberate negative control proves the generated input is needed by scanning.
    missing = invoke(scan_command, expect_failure=True)
    require("generated.hpp" in missing.stderr, "Missing-input scan failed for an unrelated reason")

    def generate(value):
        (work / "generated.hpp").write_text("#define P04_BASE {}\n".format(value))
        print("[generate] generated.hpp: P04_BASE={}".format(value))

    def scan(stage):
        require((work / "generated.hpp").is_file(), "Generate inputs before scanning")
        result = invoke(scan_command)
        data = json.loads(result.stdout)
        (work / (stage + ".p1689.json")).write_text(result.stdout)
        validate_scan(data, work)

    partition = "-fmodule-file=p04:value=" + str(work / "value.pcm")
    interface = "-fmodule-file=p04=" + str(work / "api.pcm")

    def build(stage, expected):
        scan(stage)
        invoke(base + ["--precompile", "value.cppm", "-MD", "-MF", "value.d",
                       "-o", "value.pcm"])
        require("generated.hpp" in (work / "value.d").read_text(),
                "Generated header absent from compiler depfile")
        invoke(base + ["--precompile", "api.cppm", partition, "-o", "api.pcm"])
        invoke(base + ["-c", "value.pcm", "-o", "value.o"])
        invoke(base + ["-c", "api.pcm", partition, "-o", "api.o"])
        invoke(base + ["-c", "main.cpp", partition, interface, "-o", "main.o"])
        invoke([compiler, "value.o", "api.o", "main.o", "-o", "app"])
        result = invoke([work / "app"])
        require(result.stdout == str(expected) + "\n", "Incorrect executable output at " + stage)
        hashes = {name: digest(work / name) for name in ("value.pcm", "api.pcm")}
        report["stages"].append({"name": stage, "output": result.stdout, "bmi_sha256": hashes})
        print("[stage] {}: output={}, BMI hashes={}".format(stage, expected, hashes))
        return hashes

    generate(40)
    initial = build("initial", 42)
    # Edit the real partition, leaving the primary interface and importer untouched.
    source = work / "value.cppm"
    source.write_text(source.read_text().replace("P04_BASE + 2", "P04_BASE + 3"))
    edited = build("partition-edit", 43)
    require(all(initial[name] != edited[name] for name in initial),
            "Partition edit must regenerate both partition and transitive interface BMI")
    generate(50)
    generated = build("generated-edit", 53)
    require(all(edited[name] != generated[name] for name in edited),
            "Generated header edit must propagate into both BMIs")
    for name in ("value.pcm", "api.pcm", "value.o", "api.o", "main.o", "app"):
        (work / name).unlink()
    build("clean-rebuild", 53)
    print("PASS: explicit Clang module prototype (not production CXX module support)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default=default_compiler(),
                        help="Clang driver (default: environment, known LLVM path, then PATH)")
    parser.add_argument("--scanner", help="Matching clang-scan-deps (default: beside compiler)")
    parser.add_argument("--keep", action="store_true", help="Keep isolated artifacts and report.json")
    args = parser.parse_args()
    (ROOT / ".runs").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="p04 ", dir=ROOT / ".runs"))
    report = {"platform": platform.platform(), "machine": platform.machine(),
              "python": sys.version, "commands": [], "stages": [], "status": "FAIL"}
    print("[platform] " + report["platform"])
    print("[work] " + str(work))
    try:
        probe(args, work, report)
        report["status"] = "PASS"
        return 0
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        report["error"] = str(error)
        print("FAIL: " + str(error), file=sys.stderr)
        return 1
    finally:
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if args.keep:
            print("[report] " + str(work / "report.json"))
        else:
            shutil.rmtree(work)


if __name__ == "__main__":
    sys.exit(main())
