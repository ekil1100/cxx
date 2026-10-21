#!/usr/bin/env python3
"""Real-compiler M1 library tests: --cxx /absolute/cxx [--compiler clang++]."""

import itertools
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

import pytest

pytestmark = pytest.mark.e2e

CXX = None
COMPILER = None
CONFIG = '[preset]\nname="llvm"\n[cpp]\nversion="17"\n'


@unittest.skipUnless(
    sys.platform.startswith("linux") or sys.platform == "darwin",
    "M1 shared runtime layout covers Linux and macOS",
)
class Libraries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="cxx m1 libraries ")
        cls.project = pathlib.Path(cls.temporary.name).resolve()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.env = os.environ.copy()
        self.env["CXX_BOOTSTRAP_COMPILER"] = COMPILER
        self.env.pop("CXX_BOOTSTRAP_ARCHIVER", None)
        # Neither the build runner nor direct execution may rely on loader overrides.
        for name in list(self.env):
            if name.startswith(("LD_", "DYLD_")):
                del self.env[name]
        # Keep the host runtime cache, but isolate target artifacts and fixtures.
        for path in self.project.iterdir():
            if path.name == ".cxx":
                shutil.rmtree(path / "build", ignore_errors=True)
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        self.put("cxxrc.toml", CONFIG)

    def put(self, name, text):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def declare(self, body):
        self.put(
            "build.cpp",
            "#include <cxx/cxx.hpp>\nvoid build(cxx::Build& b) {\n" + body + "\n}\n",
        )

    def command(self, args, expected=0, cwd=None):
        result = subprocess.run(
            list(map(str, args)),
            cwd=cwd or self.project,
            env=self.env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if expected is None:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def invoke(self, *args, expected=0):
        return self.command([CXX, *args], expected)

    def counts(self, result):
        match = re.search(r"compiled=(\d+) archived=(\d+) linked=(\d+)", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return tuple(map(int, match.groups()))

    def commands(self, result):
        return [
            shlex.split(line[len("[command] ") :])
            for line in result.stderr.splitlines()
            if line.startswith("[command] ")
        ]

    def link_command(self, result, target="app"):
        commands = [
            cmd
            for cmd in self.commands(result)
            if "-o" in cmd
            and "-c" not in cmd
            and "-E" not in cmd
            and pathlib.Path(cmd[cmd.index("-o") + 1]).parent.name == target
        ]
        self.assertEqual(len(commands), 1, result.stderr)
        return commands[0]

    def object_inputs(self, command, target):
        directory = self.project / ".cxx/build" / target / ".objects"
        return [
            arg
            for arg in command
            if pathlib.Path(arg).parent == directory and arg.endswith(".o")
        ]

    def library_inputs(self, command, target):
        directory = self.project / ".cxx/build" / target
        return [arg for arg in command if pathlib.Path(arg).parent == directory]

    def main(self, expression, declarations="int value();"):
        self.put(
            "main.cpp",
            "#include <cstdio>\n"
            + declarations
            + '\nint main() { printf("%d\\n", '
            + expression
            + "); }\n",
        )

    def test_direct_object_selection_and_incremental_edit(self):
        self.declare("""auto& obj = b.object_library("obj");
obj.source("one.cpp").source("two.cpp");
if (obj.kind() != cxx::TargetKind::object_library) throw std::runtime_error("wrong kind");
b.executable("app").source("main.cpp").link(obj);
""")
        self.put("one.cpp", "int value() { return 1; }\n")
        self.put("two.cpp", "int second() { return 2; }\n")
        self.main("value() + second()", "int value(); int second();")
        self.assertEqual(self.counts(self.invoke("build", "obj", "-v")), (2, 0, 0))
        root = self.project / ".cxx/build"
        self.assertEqual(len(list((root / "obj/.objects").glob("*.o"))), 2)
        self.assertFalse(list((root / "obj").glob("*.a")))
        self.assertFalse(list((root / "obj").glob("*.so")))
        self.assertFalse(list((root / "obj").glob("*.dylib")))
        self.assertFalse((root / "app/app").exists())
        self.assertEqual(self.counts(self.invoke("build", "obj")), (0, 0, 0))
        self.put("one.cpp", "int value() { return 4; }\n")
        self.assertEqual(self.counts(self.invoke("build", "obj")), (1, 0, 0))
        result = self.invoke("run", "app", "-v")
        self.assertEqual(result.stdout, "6\n")
        self.assertEqual(self.counts(result), (1, 0, 1))
        self.assertEqual(len(self.object_inputs(self.link_command(result), "obj")), 2)

    def test_nested_diamond_objects_are_injected_once(self):
        self.declare("""auto& leaf = b.object_library("leaf").source("leaf.cpp");
auto& left = b.object_library("left").source("left.cpp").link(leaf);
auto& right = b.object_library("right").source("right.cpp").link(leaf);
b.executable("app").source("main.cpp").link(left).link(right).link(leaf);
""")
        self.put("leaf.cpp", "int value() { return 3; }\n")
        for side in ("left", "right"):
            self.put(
                side + ".cpp", "int value(); int " + side + "() { return value(); }\n"
            )
        self.main("left() + right() + value()", "int left(); int right(); int value();")
        result = self.invoke("run", "-v")
        self.assertEqual(result.stdout, "9\n")
        self.assertEqual(self.counts(result), (4, 0, 1))
        command = self.link_command(result)
        for target in ("leaf", "left", "right"):
            self.assertEqual(len(self.object_inputs(command, target)), 1)
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_static_and_shared_absorb_objects_without_reinjection(self):
        for kind in ("static_library", "shared_library"):
            with self.subTest(kind=kind):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                self.declare(
                    """auto& obj = b.object_library("obj").source("obj.cpp");
auto& lib = b.%s("lib").source("lib.cpp").link(obj, cxx::Visibility::public_);
b.executable("app").source("main.cpp").link(lib);
"""
                    % kind
                )
                self.put("obj.cpp", "int value() { return 4; }\n")
                self.put(
                    "lib.cpp", "int value(); int wrapped() { return value() + 1; }\n"
                )
                self.main("wrapped() + value()", "int wrapped(); int value();")
                result = self.invoke("run", "-v")
                self.assertEqual(result.stdout, "9\n")
                self.assertEqual(
                    self.counts(result),
                    (3, 1, 1) if kind == "static_library" else (3, 0, 2),
                )
                self.assertEqual(
                    self.object_inputs(self.link_command(result), "obj"), []
                )
                if kind == "shared_library":
                    producer = self.link_command(result, "lib")
                else:
                    archives = [
                        cmd
                        for cmd in self.commands(result)
                        if "rcs" in cmd
                        and pathlib.Path(cmd[cmd.index("rcs") + 1]).parent
                        == self.project / ".cxx/build/lib"
                    ]
                    self.assertEqual(len(archives), 1, result.stderr)
                    producer = archives[0]
                self.assertEqual(len(self.object_inputs(producer, "obj")), 1)
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
                self.put("obj.cpp", "int value() { return 7; }\n")
                edited = self.invoke("run")
                self.assertEqual(edited.stdout, "15\n")
                self.assertEqual(self.counts(edited)[0], 1)
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_shared_private_chain_runs_after_build_tree_relocation(self):
        self.declare("""auto& leaf = b.shared_library("leaf").source("leaf.cpp")
    .include("secret", cxx::Visibility::public_).define("SECRET=7", cxx::Visibility::public_);
auto& middle = b.shared_library("middle").source("middle.cpp").link(leaf);
auto& top = b.shared_library("top").source("top.cpp").link(middle);
if (top.kind() != cxx::TargetKind::shared_library) throw std::runtime_error("wrong kind");
b.executable("app").source("main.cpp").link(top);
""")
        self.put("secret/secret.hpp", "int leaf();\n")
        self.put("leaf.cpp", "int data = SECRET; int leaf() { return data; }\n")
        self.put(
            "middle.cpp",
            "#include <secret.hpp>\nint middle() { return leaf() + SECRET; }\n",
        )
        guard = "#if defined(SECRET) || __has_include(<secret.hpp>)\n#error Private usage leaked\n#endif\n"
        self.put(
            "top.cpp", guard + "int middle(); int value() { return middle() + 1; }\n"
        )
        self.main("value()")
        self.put("main.cpp", guard + (self.project / "main.cpp").read_text())
        result = self.invoke("run", "-v")
        self.assertEqual(result.stdout, "15\n")
        self.assertEqual(self.counts(result), (4, 0, 4))
        for target, hidden in (("app", ("leaf", "middle")), ("top", ("leaf",))):
            command = self.link_command(result, target)
            for dependency in hidden:
                self.assertFalse(
                    any(
                        pathlib.Path(arg).parent
                        == self.project / ".cxx/build" / dependency
                        for arg in command
                    ),
                    command,
                )
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
        self.put("leaf.cpp", "int data = SECRET + 2; int leaf() { return data; }\n")
        edited = self.invoke("run")
        self.assertEqual(edited.stdout, "17\n")
        self.assertEqual(self.counts(edited)[0], 1)
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
        # Move, rather than copy: absolute references into the old tree must fail.
        moved = self.project / "relocated build tree"
        shutil.move(str(self.project / ".cxx/build"), str(moved))
        self.assertEqual(
            self.command([moved / "app/app"], cwd=self.project.parent).stdout, "17\n"
        )

    def test_object_compile_visibility(self):
        self.declare("""auto& obj = b.object_library("obj").source("obj.cpp")
    .include("public", cxx::Visibility::public_).define("PUBLIC=2", cxx::Visibility::public_)
    .include("private").define("PRIVATE=3")
    .include("interface", cxx::Visibility::interface_).define("CLIENT=5", cxx::Visibility::interface_);
b.executable("app").source("main.cpp").link(obj);
""")
        for directory in ("public", "private", "interface"):
            self.put(directory + "/" + directory + ".hpp", "#pragma once\n")
        self.put(
            "obj.cpp",
            """#include <public.hpp>
#include <private.hpp>
#if defined(CLIENT) || __has_include(<interface.hpp>)
#error Interface usage applied locally
#endif
int value() { return PUBLIC + PRIVATE; }
""",
        )
        self.put(
            "main.cpp",
            """#include <public.hpp>
#include <interface.hpp>
#include <cstdio>
#if defined(PRIVATE) || __has_include(<private.hpp>)
#error Private usage leaked
#endif
int value(); int main() { printf("%d\\n", value() + PUBLIC + CLIENT); }
""",
        )
        self.assertEqual(self.invoke("run").stdout, "12\n")

    def test_private_static_dependency_is_link_only(self):
        self.declare("""auto& leaf = b.shared_library("leaf").source("leaf.cpp")
    .include("secret", cxx::Visibility::public_).define("SECRET=6", cxx::Visibility::public_);
auto& lib = b.static_library("lib").source("lib.cpp").link(leaf);
b.executable("app").source("main.cpp").link(lib);
""")
        self.put("secret/secret.hpp", "int leaf();\n")
        self.put("leaf.cpp", "int leaf() { return SECRET; }\n")
        self.put(
            "lib.cpp",
            "#include <secret.hpp>\nint value() { return leaf() + SECRET; }\n",
        )
        self.main("value()")
        self.put(
            "main.cpp",
            "#if defined(SECRET) || __has_include(<secret.hpp>)\n"
            "#error Private static compile usage leaked\n#endif\n"
            + (self.project / "main.cpp").read_text(),
        )
        result = self.invoke("run", "-v")
        self.assertEqual(result.stdout, "12\n")
        command = self.link_command(result)
        self.assertTrue(
            any(
                pathlib.Path(arg).parent == self.project / ".cxx/build/leaf"
                for arg in command
            ),
            command,
        )

    def test_interface_link_edges_only_affect_consumers(self):
        for kind in ("object_library", "static_library", "shared_library"):
            with self.subTest(dependency=kind):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                self.declare(
                    """auto& dep = b.%s("dep").source("dep.cpp")
    .define("CLIENT=8", cxx::Visibility::public_).include("client", cxx::Visibility::public_);
auto& facade = b.shared_library("facade").source("facade.cpp")
    .link(dep, cxx::Visibility::interface_);
b.executable("app").source("main.cpp").link(facade);
"""
                    % kind
                )
                self.put("client/client.hpp", "int value();\n")
                self.put("dep.cpp", "int value() { return CLIENT; }\n")
                self.put(
                    "facade.cpp",
                    """#if defined(CLIENT) || __has_include(<client.hpp>)
#error Interface edge applied locally
#endif
int facade() { return 1; }
""",
                )
                self.put(
                    "main.cpp",
                    "#include <client.hpp>\n#include <cstdio>\n"
                    'int facade(); int main() { printf("%d\\n", value() + facade() + CLIENT); }\n',
                )
                result = self.invoke("run", "-v")
                self.assertEqual(result.stdout, "17\n")
                producer = self.link_command(result, "facade")
                self.assertEqual(self.object_inputs(producer, "dep"), [])
                self.assertFalse(
                    any(
                        pathlib.Path(arg).parent == self.project / ".cxx/build/dep"
                        for arg in producer
                    ),
                    producer,
                )
                consumer = self.link_command(result)
                if kind == "object_library":
                    self.assertEqual(len(self.object_inputs(consumer, "dep")), 1)
                else:
                    self.assertTrue(
                        any(
                            pathlib.Path(arg).parent == self.project / ".cxx/build/dep"
                            for arg in consumer
                        ),
                        consumer,
                    )

    def test_mixed_static_diamond_order_and_absorbed_objects(self):
        self.declare("""auto& obj = b.object_library("obj").source("obj.cpp");
auto& base = b.static_library("base").source("base.cpp").link(obj, cxx::Visibility::public_);
auto& left = b.static_library("left").source("left.cpp").link(base);
auto& right = b.static_library("right").source("right.cpp").link(base);
auto& shared = b.shared_library("shared").source("shared.cpp");
b.executable("app").source("main.cpp").link(left).link(shared).link(right).link(obj);
""")
        self.put("obj.cpp", "int object_value() { return 2; }\n")
        self.put(
            "base.cpp",
            "int object_value(); int base() { return object_value() + 1; }\n",
        )
        for side in ("left", "right"):
            self.put(
                side + ".cpp", "int base(); int " + side + "() { return base(); }\n"
            )
        self.put("shared.cpp", "int shared() { return 4; }\n")
        self.main(
            "left() + right() + shared()", "int left(); int right(); int shared();"
        )
        result = self.invoke("run", "-v")
        self.assertEqual(result.stdout, "10\n")
        self.assertEqual(self.counts(result), (6, 3, 2))
        command = self.link_command(result)
        objects = self.object_inputs(command, "obj")
        self.assertEqual(len(objects), 1)
        archive_paths = [arg for arg in command if arg.endswith(".a")]
        for archive in archive_paths:
            self.assertLess(command.index(objects[0]), command.index(archive))
        archives = [pathlib.Path(arg).name for arg in archive_paths]
        self.assertEqual(sorted(archives), ["libbase.a", "libleft.a", "libright.a"])
        for side in ("left", "right"):
            self.assertLess(
                archives.index("lib" + side + ".a"), archives.index("libbase.a")
            )

    def test_explicit_objects_deduplicate_against_direct_shared_owner(self):
        self.declare("""auto& obj = b.object_library("obj").source("obj.cpp");
auto& shared = b.shared_library("shared").link(obj);
b.executable("app").source("main.cpp").link(shared).link(obj);
""")
        self.put("obj.cpp", "int value() { return 7; }\n")
        self.main("value()")
        result = self.invoke("run", "-v")
        self.assertEqual(result.stdout, "7\n")
        self.assertEqual(self.object_inputs(self.link_command(result), "obj"), [])
        self.assertEqual(
            len(self.object_inputs(self.link_command(result, "shared"), "obj")), 1
        )

    def test_private_shared_dependency_does_not_own_objects_for_consumer(self):
        self.declare("""auto& obj = b.object_library("obj").source("obj.cpp");
auto& inner = b.shared_library("inner").link(obj);
auto& outer = b.shared_library("outer").source("outer.cpp").link(inner);
b.executable("app").source("main.cpp").link(outer).link(obj);
""")
        self.put("obj.cpp", "int value() { return 7; }\n")
        self.put("outer.cpp", "int value(); int outer() { return value() + 2; }\n")
        self.main("value() + outer()", "int value(); int outer();")
        result = self.invoke("run", "-v")
        self.assertEqual(result.stdout, "16\n")
        command = self.link_command(result)
        self.assertEqual(len(self.object_inputs(command, "obj")), 1)
        self.assertFalse(
            any(
                pathlib.Path(arg).parent == self.project / ".cxx/build/inner"
                for arg in command
            ),
            command,
        )
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_public_objects_survive_private_shared_deduplication(self):
        paths = {
            "direct": ["outer.link(obj, cxx::Visibility::public_);"],
            "wrapped": ["outer.link(wrap, cxx::Visibility::public_);"],
            "split": [
                "outer.link(obj);",
                "outer.link(obj, cxx::Visibility::interface_);",
            ],
            "split_wrapped": [
                "outer.link(wrap);",
                "outer.link(obj, cxx::Visibility::interface_);",
            ],
        }
        for path, visibility, reverse in itertools.product(
            paths, ("private_", "public_", None), (False, True)
        ):
            with self.subTest(path=path, inner_visibility=visibility, reverse=reverse):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                body = 'auto& obj = b.object_library("obj").source("obj.cpp");\n'
                wrapped = "wrapped" in path
                if wrapped:
                    body += 'auto& wrap = b.object_library("wrap").source("wrap.cpp").link(obj);\n'
                if visibility:
                    body += 'auto& inner = b.shared_library("inner").link(obj);\n'
                body += 'auto& outer = b.shared_library("outer").source("outer.cpp");\n'
                edges = paths[path].copy()
                if visibility:
                    edges.insert(
                        0, f"outer.link(inner, cxx::Visibility::{visibility});"
                    )
                if reverse:
                    edges.reverse()
                self.declare(
                    body
                    + "\n".join(edges)
                    + '\nb.executable("app").source("main.cpp").link(outer);'
                )
                self.put("obj.cpp", "int value() { return 7; }\n")
                self.put("wrap.cpp", "int wrapped() { return 1; }\n")
                self.put("outer.cpp", "int outer() { return 2; }\n")
                self.main("value()")
                result = self.invoke("run", "app", "-v")
                self.assertEqual(result.stdout, "7\n")
                outer = self.link_command(result, "outer")
                app = self.link_command(result)
                self.assertEqual(
                    len(self.object_inputs(outer, "obj")), int(visibility is None)
                )
                self.assertEqual(
                    len(self.object_inputs(app, "obj")), int(visibility == "private_")
                )
                self.assertEqual(len(self.object_inputs(outer, "wrap")), int(wrapped))
                self.assertEqual(self.object_inputs(app, "wrap"), [])
                inner_inputs = [
                    arg
                    for arg in app
                    if pathlib.Path(arg).parent == self.project / ".cxx/build/inner"
                ]
                self.assertEqual(len(inner_inputs), int(visibility == "public_"))
                if visibility:
                    self.assertEqual(
                        len(
                            self.object_inputs(
                                self.link_command(result, "inner"), "obj"
                            )
                        ),
                        1,
                    )
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_forwarded_objects_keep_libraries_and_downstream_order(self):
        for kind in ("object_library", "static_library", "shared_library"):
            with self.subTest(downstream=kind):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                self.declare(
                    """auto& base = b.static_library("base").source("base.cpp");
auto& dep = b.static_library("dep").source("dep.cpp").link(base);
auto& obj = b.object_library("obj").source("obj.cpp").link(dep);
auto& inner = b.shared_library("inner").link(obj);
auto& outer = b.shared_library("outer").source("outer.cpp")
    .link(inner).link(obj, cxx::Visibility::public_);
auto& relay = b.%s("relay").source("relay.cpp").link(outer, cxx::Visibility::public_);
b.executable("app").source("main.cpp").link(base).link(relay);
"""
                    % kind
                )
                self.put("base.cpp", "int base() { return 3; }\n")
                self.put("dep.cpp", "int base(); int dep() { return base() + 4; }\n")
                self.put("obj.cpp", "int dep(); int value() { return dep(); }\n")
                self.put("outer.cpp", "int outer() { return 2; }\n")
                self.put("relay.cpp", "int relay() { return 1; }\n")
                self.main("value()")
                result = self.invoke("run", "-v")
                self.assertEqual(result.stdout, "7\n")
                self.assertEqual(
                    self.object_inputs(self.link_command(result, "outer"), "obj"), []
                )
                app = self.link_command(result)
                self.assertEqual(
                    len(self.object_inputs(app, "obj")), int(kind == "object_library")
                )
                # The shared relay resolves the forwarded object's private libraries;
                # an object/static relay must retain them in its exported link inputs.
                consumer = (
                    self.link_command(result, "relay")
                    if kind == "shared_library"
                    else app
                )
                dep = str(self.project / ".cxx/build/dep/libdep.a")
                base = str(self.project / ".cxx/build/base/libbase.a")
                self.assertEqual(consumer.count(dep), 1)
                self.assertEqual(consumer.count(base), 1)
                self.assertLess(consumer.index(dep), consumer.index(base))
                outer = [
                    arg
                    for arg in consumer
                    if pathlib.Path(arg).parent == self.project / ".cxx/build/outer"
                ]
                self.assertEqual(len(outer), 1)
                self.assertLess(consumer.index(outer[0]), consumer.index(dep))
                if kind == "static_library":
                    relay = str(self.project / ".cxx/build/relay/librelay.a")
                    self.assertLess(consumer.index(relay), consumer.index(outer[0]))
                if kind == "shared_library":
                    self.assertNotIn(dep, app)
                if kind != "static_library":
                    objects = self.object_inputs(consumer, "obj")
                    self.assertEqual(len(objects), 1)
                    self.assertLess(consumer.index(objects[0]), consumer.index(dep))
                self.assertFalse(
                    any(
                        pathlib.Path(arg).parent == self.project / ".cxx/build/inner"
                        for arg in consumer
                    ),
                    consumer,
                )
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
                self.put("base.cpp", "int base() { return 5; }\n")
                edited = self.invoke("run")
                self.assertEqual(edited.stdout, "9\n")
                self.assertEqual(self.counts(edited)[0], 1)
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_public_shared_owner_satisfies_downstream_static_object_interface(self):
        self.declare("""auto& dep = b.static_library("dep").source("dep.cpp");
auto& obj = b.object_library("obj").source("obj.cpp").link(dep);
auto& inner = b.shared_library("inner").link(obj);
auto& outer = b.shared_library("outer").source("outer.cpp")
    .link(inner, cxx::Visibility::public_).link(obj, cxx::Visibility::public_);
auto& relay = b.static_library("relay").source("relay.cpp").link(outer);
b.executable("app").source("main.cpp").link(relay);
""")
        self.put("dep.cpp", "int dep() { return 7; }\n")
        self.put("obj.cpp", "int dep(); int value() { return dep(); }\n")
        self.put("outer.cpp", "int outer() { return 2; }\n")
        self.put("relay.cpp", "int relay() { return 1; }\n")
        self.main("value()")
        result = self.invoke("run", "-v")
        self.assertEqual(result.stdout, "7\n")
        archives = [
            cmd
            for cmd in self.commands(result)
            if "rcs" in cmd
            and pathlib.Path(cmd[cmd.index("rcs") + 1]).name.startswith("librelay.a")
        ]
        self.assertEqual(len(archives), 1, result.stderr)
        self.assertEqual(self.object_inputs(archives[0], "obj"), [])
        app = self.link_command(result)
        self.assertEqual(self.object_inputs(app, "obj"), [])
        self.assertNotIn(str(self.project / ".cxx/build/dep/libdep.a"), app)
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_explicit_objects_preserve_constructor_only_archive_members(self):
        for kind in ("executable", "shared_library"):
            with self.subTest(consumer=kind):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                body = """auto& obj = b.object_library("obj").source("registration.cpp");
auto& archive = b.static_library("archive").link(obj);
"""
                if kind == "executable":
                    body += 'b.executable("app").source("main.cpp").link(archive).link(obj);'
                    owner = "app"
                else:
                    body += """auto& shared = b.shared_library("shared").source("shared.cpp").link(archive).link(obj);
b.executable("app").source("main.cpp").link(shared);
"""
                    owner = "shared"
                self.declare(body)
                self.put(
                    "registration.cpp",
                    "#include <cstdio>\nnamespace { struct Registration {\n"
                    'Registration() { puts("registered"); }\n} registration; }\n',
                )
                self.put("shared.cpp", "int value() { return 7; }\n")
                self.main("value()" if kind == "shared_library" else "7")
                result = self.invoke("run", "-v")
                self.assertEqual(result.stdout, "registered\n7\n")
                command = self.link_command(result, owner)
                objects = self.object_inputs(command, "obj")
                self.assertEqual(len(objects), 1)
                archive = str(self.project / ".cxx/build/archive/libarchive.a")
                self.assertLess(command.index(objects[0]), command.index(archive))
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_lazy_archive_members_are_not_assumed_present_in_shared_library(self):
        for direct_object_in_shared in (False, True):
            with self.subTest(direct_object_in_shared=direct_object_in_shared):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                shared_objects = ".link(obj)" if direct_object_in_shared else ""
                app_objects = "" if direct_object_in_shared else ".link(obj)"
                self.declare(
                    """auto& obj = b.object_library("obj").source("obj.cpp");
auto& archive = b.static_library("archive").link(obj);
auto& shared = b.shared_library("shared").source("shared.cpp").link(archive)%s;
b.executable("app").source("main.cpp").link(shared)%s;
"""
                    % (shared_objects, app_objects)
                )
                self.put("obj.cpp", "int value() { return 7; }\n")
                # No reference forces the linker to extract value() from archive.
                self.put("shared.cpp", "int shared_value() { return 2; }\n")
                self.main(
                    "value() + shared_value()", "int value(); int shared_value();"
                )
                result = self.invoke("run", "-v")
                self.assertEqual(result.stdout, "9\n")
                owner = "shared" if direct_object_in_shared else "app"
                self.assertEqual(
                    len(self.object_inputs(self.link_command(result, owner), "obj")), 1
                )
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_forwarded_object_provider_survives_shared_relay(self):
        self.declare("""auto& leaf = b.object_library("leaf").source("leaf.cpp");
auto& owner = b.shared_library("owner").link(leaf);
auto& wrap = b.object_library("wrap").source("wrap.cpp").link(owner);
auto& facade = b.shared_library("facade").source("facade.cpp")
    .link(wrap, cxx::Visibility::interface_).link(leaf, cxx::Visibility::interface_);
auto& relay = b.shared_library("relay").link(facade, cxx::Visibility::public_);
b.executable("app").source("main.cpp").link(relay);
""")
        self.put("leaf.cpp", "int value() { return 7; }\n")
        self.put("wrap.cpp", "int value(); int wrapped() { return value(); }\n")
        self.put("facade.cpp", "int facade() { return 1; }\n")
        self.main("value()")
        result = self.invoke("run", "-v")
        self.assertEqual(result.stdout, "7\n")
        app = self.link_command(result)
        self.assertEqual(self.object_inputs(app, "leaf"), [])
        self.assertEqual(len(self.library_inputs(app, "owner")), 1)
        self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_split_object_interface_resolves_private_library(self):
        forms = (
            ".link(obj).link(obj, cxx::Visibility::interface_)",
            ".link(obj, cxx::Visibility::interface_).link(obj)",
            ".link(obj, cxx::Visibility::public_)",
        )
        for form in forms:
            with self.subTest(form=form):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                self.declare(
                    """static_assert(__cplusplus == 201703L);
auto& secret = b.shared_library("secret").source("secret.cpp")
    .define("SECRET_USAGE=1", cxx::Visibility::public_);
auto& obj = b.object_library("obj").source("obj.cpp").link(secret);
auto& facade = b.shared_library("facade")%s;
b.executable("app").source("main.cpp").link(facade);
"""
                    % form
                )
                self.put("secret.cpp", "int secret() { return 7; }\n")
                self.put("obj.cpp", "int secret(); int value() { return secret(); }\n")
                self.main("value()")
                self.put(
                    "main.cpp",
                    "#ifdef SECRET_USAGE\n#error Private usage leaked\n#endif\n"
                    + (self.project / "main.cpp").read_text(),
                )
                result = self.invoke("run", "-v")
                self.assertEqual(result.stdout, "7\n")
                app = self.link_command(result)
                self.assertEqual(self.library_inputs(app, "secret"), [])
                self.assertEqual(self.object_inputs(app, "obj"), [])
                self.assertEqual(
                    len(self.object_inputs(self.link_command(result, "facade"), "obj")),
                    1,
                )
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))
                self.main("secret()", "int secret();")
                failure = self.invoke("build", expected=None)
                self.assertIn("secret", failure.stderr)
                self.assertRegex(failure.stderr.lower(), "undefined|unresolved")

    def test_object_provider_survives_nested_wrappers_and_repeated_relays(self):
        for relay_count, reverse in itertools.product((0, 3), (False, True)):
            with self.subTest(relays=relay_count, reverse=reverse):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                body = """static_assert(__cplusplus == 201703L);
auto& extra = b.shared_library("extra").source("extra.cpp");
auto& leaf = b.object_library("leaf").source("leaf.cpp").source("registration.cpp")
    .define("LEAF_USAGE=1", cxx::Visibility::public_);
auto& owner = b.shared_library("owner").link(leaf).link(extra, cxx::Visibility::public_)
    .define("OWNER_USAGE=1", cxx::Visibility::public_);
auto& wrap = b.object_library("wrap").source("wrap.cpp").link(owner);
auto& nested = b.object_library("nested").source("nested.cpp").link(wrap);
auto& facade = b.shared_library("facade").source("facade.cpp");
"""
                edges = [
                    "facade.link(nested, cxx::Visibility::interface_);",
                    "facade.link(leaf, cxx::Visibility::interface_);",
                ]
                body += "\n".join(reversed(edges) if reverse else edges)
                previous = "facade"
                for index in range(relay_count):
                    relay = f"relay{index}"
                    source = "" if index == 0 else f'.source("{relay}.cpp")'
                    body += (
                        f'\nauto& {relay} = b.shared_library("{relay}"){source}'
                        f".link({previous}, cxx::Visibility::public_);"
                    )
                    self.put(f"{relay}.cpp", f"int {relay}() {{ return 0; }}\n")
                    previous = relay
                body += f'\nb.executable("app").source("main.cpp").link({previous});'
                self.declare(body)
                self.put("extra.cpp", "int extra() { return 2; }\n")
                self.put("leaf.cpp", "int value() { return 7; }\n")
                self.put(
                    "registration.cpp",
                    "#include <cstdio>\nnamespace { struct Registration {\n"
                    'Registration() { puts("leaf-init"); }\n} registration; }\n',
                )
                self.put(
                    "wrap.cpp",
                    "#ifndef OWNER_USAGE\n#error Missing local usage\n#endif\n"
                    "int value(); int wrapped() { return value(); }\n",
                )
                self.put(
                    "nested.cpp",
                    "#include <cstdio>\nnamespace { struct Registration {\n"
                    'Registration() { puts("nested-init"); }\n} registration; }\n',
                )
                self.put("facade.cpp", "int facade() { return 1; }\n")
                self.main("value() + extra()", "int value(); int extra();")
                self.put(
                    "main.cpp",
                    "#if !defined(LEAF_USAGE) || defined(OWNER_USAGE)\n"
                    "#error Incorrect compile visibility\n#endif\n"
                    + (self.project / "main.cpp").read_text(),
                )
                result = self.invoke("run", "-v")
                self.assertEqual(result.stdout.splitlines()[-1], "9")
                self.assertCountEqual(
                    result.stdout.splitlines()[:-1], ["leaf-init", "nested-init"]
                )
                consumers = ["app", *(f"relay{i}" for i in range(relay_count))]
                for consumer in consumers:
                    command = self.link_command(result, consumer)
                    self.assertEqual(self.object_inputs(command, "leaf"), [])
                    self.assertEqual(len(self.library_inputs(command, "owner")), 1)
                    self.assertEqual(len(self.library_inputs(command, "extra")), 1)
                    for obj in ("wrap", "nested"):
                        self.assertEqual(
                            len(self.object_inputs(command, obj)),
                            int(consumer == ("relay0" if relay_count else "app")),
                        )
                self.assertEqual(
                    len(self.object_inputs(self.link_command(result, "owner"), "leaf")),
                    2,
                )
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_partial_object_interface_resolves_only_stored_groups(self):
        for relay_count, same_secret in itertools.product((0, 2), (False, True)):
            with self.subTest(relays=relay_count, same_secret=same_secret):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                pending_secret = "hidden" if same_secret else "needed"
                body = (
                    """auto& hidden = b.shared_library("hidden").source("hidden.cpp")
    .define("SECRET_USAGE=1", cxx::Visibility::public_);
auto& needed = b.shared_library("needed").source("needed.cpp")
    .define("SECRET_USAGE=1", cxx::Visibility::public_);
auto& stored = b.object_library("stored").source("stored.cpp").link(hidden);
auto& pending = b.object_library("pending").source("pending.cpp").link(%s);
auto& mixed = b.object_library("mixed").source("mixed.cpp").link(stored).link(pending);
auto& facade = b.shared_library("facade");
"""
                    % pending_secret
                )
                edges = [
                    "facade.link(stored);",
                    "facade.link(mixed, cxx::Visibility::interface_);",
                ]
                body += "\n".join(reversed(edges) if same_secret else edges)
                previous = "facade"
                for index in range(relay_count):
                    relay = f"relay{index}"
                    source = "" if index == 0 else f'.source("{relay}.cpp")'
                    body += (
                        f'\nauto& {relay} = b.shared_library("{relay}"){source}'
                        f".link({previous}, cxx::Visibility::public_);"
                    )
                    self.put(f"{relay}.cpp", f"int {relay}() {{ return 0; }}\n")
                    previous = relay
                body += f'\nb.executable("app").source("main.cpp").link({previous});'
                self.declare(body)
                self.put("hidden.cpp", "int hidden() { return 7; }\n")
                self.put("needed.cpp", "int needed() { return 11; }\n")
                for obj, dependency in (
                    ("stored", "hidden"),
                    ("pending", pending_secret),
                ):
                    self.put(
                        f"{obj}.cpp",
                        f"int {dependency}(); int {obj}() {{ return {dependency}(); }}\n"
                        "#include <cstdio>\nnamespace { struct Registration {\n"
                        f'Registration() {{ puts("{obj}-init"); }}\n}} registration; }}\n',
                    )
                self.put(
                    "mixed.cpp",
                    "int stored(); int pending(); int value() { return stored() + pending(); }\n",
                )
                self.main("value()")
                self.put(
                    "main.cpp",
                    "#ifdef SECRET_USAGE\n#error Private usage leaked\n#endif\n"
                    + (self.project / "main.cpp").read_text(),
                )
                result = self.invoke("run", "-v")
                self.assertEqual(
                    result.stdout.splitlines()[-1], "14" if same_secret else "18"
                )
                self.assertCountEqual(
                    result.stdout.splitlines()[:-1], ["stored-init", "pending-init"]
                )
                consumer = self.link_command(result, "relay0" if relay_count else "app")
                self.assertEqual(self.object_inputs(consumer, "stored"), [])
                for obj in ("pending", "mixed"):
                    self.assertEqual(len(self.object_inputs(consumer, obj)), 1)
                self.assertEqual(len(self.library_inputs(consumer, pending_secret)), 1)
                if not same_secret:
                    self.assertEqual(self.library_inputs(consumer, "hidden"), [])
                if relay_count:
                    for target in ("relay1", "app"):
                        command = self.link_command(result, target)
                        for obj in ("stored", "pending", "mixed"):
                            self.assertEqual(self.object_inputs(command, obj), [])
                        for library in ("hidden", "needed"):
                            self.assertEqual(self.library_inputs(command, library), [])
                self.assertEqual(self.counts(self.invoke("build", "app")), (0, 0, 0))

    def test_independent_public_library_survives_object_resolution(self):
        for path in ("public_", "interface_", "nested"):
            with self.subTest(path=path):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                body = """auto& secret = b.shared_library("secret").source("secret.cpp")
    .define("SECRET_USAGE=1", cxx::Visibility::public_);
auto& obj = b.object_library("obj").source("obj.cpp").link(secret);
auto& facade = b.shared_library("facade").link(obj).link(obj, cxx::Visibility::interface_);
"""
                if path == "nested":
                    body += """auto& wrap = b.object_library("wrap").source("wrap.cpp")
    .link(obj, cxx::Visibility::public_).link(secret, cxx::Visibility::public_);
facade.link(wrap, cxx::Visibility::public_);
"""
                else:
                    body += f"facade.link(secret, cxx::Visibility::{path});\n"
                body += """auto& relay = b.shared_library("relay").source("relay.cpp")
    .link(facade, cxx::Visibility::public_);
b.executable("app").source("main.cpp").link(relay);
"""
                self.declare(body)
                self.put("secret.cpp", "int secret() { return 7; }\n")
                self.put("obj.cpp", "int secret(); int value() { return secret(); }\n")
                self.put("wrap.cpp", "int wrap() { return 1; }\n")
                self.put("relay.cpp", "int relay() { return 2; }\n")
                self.main("value() + secret()", "int value(); int secret();")
                self.put(
                    "main.cpp",
                    "#ifndef SECRET_USAGE\n#error Missing public usage\n#endif\n"
                    + (self.project / "main.cpp").read_text(),
                )
                result = self.invoke("run", "-v")
                self.assertEqual(result.stdout, "14\n")
                for target in ("relay", "app"):
                    command = self.link_command(result, target)
                    self.assertEqual(len(self.library_inputs(command, "secret")), 1)
                    self.assertEqual(self.object_inputs(command, "obj"), [])
                    self.assertEqual(self.object_inputs(command, "wrap"), [])
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_source_less_object_backed_consumers(self):
        for kind in ("executable", "static_library", "shared_library"):
            with self.subTest(kind=kind):
                shutil.rmtree(self.project / ".cxx/build", ignore_errors=True)
                body = 'auto& obj = b.object_library("obj").source("obj.cpp");\n'
                if kind == "executable":
                    self.put(
                        "obj.cpp", '#include <cstdio>\nint main() { puts("9"); }\n'
                    )
                    body += 'b.executable("app").link(obj);'
                    expected = (1, 0, 1)
                else:
                    self.put("obj.cpp", "int value() { return 9; }\n")
                    self.main("value()")
                    body += (
                        "auto& lib = b." + kind + '("lib").link(obj);\n'
                        'b.executable("app").source("main.cpp").link(lib);'
                    )
                    expected = (2, 1, 1) if kind == "static_library" else (2, 0, 2)
                self.declare(body)
                result = self.invoke("run", "app")
                self.assertEqual(result.stdout, "9\n")
                self.assertEqual(self.counts(result), expected)
                self.assertEqual(self.counts(self.invoke("build")), (0, 0, 0))

    def test_invalid_run_cycle_and_empty_object_diagnostics(self):
        self.put("unit.cpp", "int value() { return 0; }\n")
        for kind in ("object_library", "shared_library"):
            with self.subTest(run=kind):
                self.declare("b." + kind + '("lib").source("unit.cpp");')
                result = self.invoke("run", "lib", expected=None)
                self.assertIn("lib", result.stderr)
                self.assertIn("not an executable", result.stderr)
        self.declare(
            'auto& a = b.object_library("a").source("unit.cpp");'
            'auto& b1 = b.shared_library("b1").source("unit.cpp");'
            "a.link(b1); b1.link(a);"
        )
        result = self.invoke("build", expected=None)
        self.assertIn("Dependency cycle", result.stderr)
        self.assertIn("a", result.stderr)
        self.assertIn("b1", result.stderr)
        self.declare('b.object_library("empty");')
        result = self.invoke("build", expected=None)
        self.assertIn("empty", result.stderr)
        self.assertRegex(result.stderr.lower(), r"no sources|no objects|empty object")
