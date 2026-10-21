#!/usr/bin/env python3
"""Generate this checkout's compilation database from CXX's actual scan commands."""

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def entries_from_log(log, root):
    entries = {}
    for line in log.splitlines():
        if not line.startswith("[command] "):
            continue
        # CXX logs C++ string literals, including octal-escaped UTF-8 bytes.
        tokens = re.findall(r'"(?:\\.|[^"\\])*"', line[len("[command] ") :])
        args = [ast.literal_eval("b" + token).decode("utf-8") for token in tokens]
        if "-E" not in args or "-MF" not in args or "-o" not in args:
            continue
        output = Path(args[args.index("-o") + 1])
        # Exclude bootstrap Runtime/Driver scans; keep declared project targets.
        if root / ".cxx/build" not in output.parents or not str(output).endswith(
            ".scan.ii"
        ):
            continue
        source = args[-3]  # compile_cpp emits: source -o scan.ii
        object_path = str(output)[: -len(".scan.ii")]
        command = []
        index = 0
        while index < len(args):
            argument = args[index]
            if argument in ("-MF", "-MT"):
                index += 2
                continue
            if argument == "-MD":
                index += 1
                continue
            if argument == "-E":
                command.append("-c")
            elif argument == "-o":
                command.extend(["-o", object_path])
                index += 2
                continue
            else:
                command.append(argument)
            index += 1
        entries[object_path] = {
            "directory": str(root),
            "file": source,
            "arguments": command,
            "output": object_path,
        }
    if not entries:
        raise RuntimeError("No project compile commands found in CXX build output")
    return list(entries.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compiler", default=os.environ.get("CXX_BOOTSTRAP_COMPILER", "clang++")
    )
    args = parser.parse_args()
    result = subprocess.run(
        [str(ROOT / "build/cxx"), "build", "-v"],
        cwd=ROOT,
        env=dict(os.environ, CXX_BOOTSTRAP_COMPILER=args.compiler),
        text=True,
        capture_output=True,
    )
    if result.returncode:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise RuntimeError("CXX build failed; compilation database left unchanged")
    entries = entries_from_log(result.stderr, ROOT)
    destination = ROOT / "compile_commands.json"
    with tempfile.TemporaryDirectory(prefix=".compdb-", dir=ROOT) as temporary:
        staged = Path(temporary) / destination.name
        staged.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        staged.replace(destination)
    print("Generated {} ({} entries)".format(destination, len(entries)))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, SyntaxError) as error:
        print("Compilation database failed: " + str(error), file=sys.stderr)
        sys.exit(1)
