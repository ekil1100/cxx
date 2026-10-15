#!/usr/bin/env python3
"""Record a small M0 full/no-op/source/header incremental baseline."""

import argparse
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--compiler")
    parser.add_argument("--units", type=int, default=12)
    args = parser.parse_args()
    if args.units < 1:
        parser.error("--units must be positive")
    selected = args.compiler or os.environ.get("CXX_BOOTSTRAP_COMPILER") or "clang++"
    compiler = shutil.which(selected)
    if not compiler:
        parser.error("Compiler not found: " + selected)
    compiler = str(pathlib.Path(compiler).absolute())
    env = os.environ.copy()
    env["CXX_BOOTSTRAP_COMPILER"] = compiler
    version = subprocess.check_output([compiler, "--version"], text=True, env=env)
    executable = args.cxx.resolve()
    results = []
    with tempfile.TemporaryDirectory(prefix="cxx-benchmark-") as temporary:
        root = pathlib.Path(temporary) / "project"
        subprocess.run(
            [str(executable), "new", str(root)],
            check=True,
            capture_output=True,
            env=env,
        )
        (root / "value.hpp").write_text("#define VALUE 1\n")
        declarations, values, sources = [], [], []
        for index in range(args.units):
            name = "unit_" + str(index)
            (root / (name + ".cpp")).write_text(
                '#include "value.hpp"\nint ' + name + "() { return VALUE; }\n"
            )
            declarations.append("int " + name + "();")
            values.append(name + "()")
            sources.append('t.source("' + name + '.cpp");')
        (root / "main.cpp").write_text(
            "\n".join(declarations)
            + "\nint main() { return ("
            + "+".join(values)
            + ") == 0; }\n"
        )
        (root / "build.cpp").write_text(
            '#include <cxx/cxx.hpp>\nvoid build(cxx::Build& b) { auto& t=b.executable("bench"); t.source("main.cpp"); '
            + " ".join(sources)
            + " }\n"
        )

        def measure(scenario):
            start = time.perf_counter()
            result = subprocess.run(
                [str(executable), "build"],
                cwd=root,
                text=True,
                capture_output=True,
                check=True,
                env=env,
            )
            elapsed = time.perf_counter() - start
            match = re.search(
                r"compiled=(\d+) archived=(\d+) linked=(\d+)", result.stderr
            )
            if not match:
                raise RuntimeError("Missing build report: " + result.stderr)
            results.append(
                {
                    "scenario": scenario,
                    "wall_seconds": elapsed,
                    "compiled": int(match[1]),
                    "archived": int(match[2]),
                    "linked": int(match[3]),
                }
            )

        measure("cold_driver_and_full_build")
        for index in range(3):
            measure("no_op_" + str(index + 1))
        (root / "unit_0.cpp").write_text(
            '#include "value.hpp"\nint unit_0() { return VALUE + 1; }\n'
        )
        measure("single_source_change")
        (root / "value.hpp").write_text("#define VALUE 2\n")
        measure("shared_header_change")
    report = {
        "schema_version": 1,
        "platform": platform.platform(),
        "translation_units": args.units + 1,
        "compiler_path": compiler,
        "compiler": version,
        "note": "M0 local wall-time baseline; no OS cache flush, resource metrics or CMake/Ninja comparison yet.",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
