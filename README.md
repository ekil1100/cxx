# CXX

Build C++ with C++.

## Purpose

CXX uses `build.cpp` to describe C++ targets and executes its own build graph. It aims to bring toolchains, dependencies, builds, and caches into one tool.

**Current status: experimental M0 implementation.** The local build loop works with Clang on POSIX systems. macOS has been tested locally; the Linux CI workflow is included but has not yet been run for this change. The full V1 design is a roadmap, not a list of shipped features.

Available now:

- `cxx new`, `cxx build`, and `cxx run`.
- Executables and static libraries, including transitive static linking.
- Source files, include directories, definitions, and private/public/interface usage requirements.
- A C++17-or-newer Build Driver directly linked with a C++23 Runtime built by the selected compiler.
- A target graph and an explicit, sequential compile/archive/link action graph.
- Content-based local incremental builds, with header discovery on each invocation.
- Target selection, useful compiler diagnostics, and program argument/exit-code forwarding.

Package management, toolchain downloads, `cxx.lock`, shared/remote caches, Modules, PCH, Unity, parallel scheduling, Query, Watch, and Windows support are future milestones.

## Installation

### Requirements

- Linux or macOS.
- Clang with the C++23 support needed to build CXX and its Runtime, plus the platform C++ standard library/SDK. User scripts and targets can still use C++17.
- `llvm-ar` or `ar`.
- Python 3.8 or later for the seed build and tests.

The implementation uses C++23 explicit object parameters for recursive lambdas, `std::ranges::contains`, and `std::string::contains`. The host compiler and standard library must support these features. Internal headers check the required library feature macros; compiling the Runtime verifies the language syntax. These requirements do not apply to the C++17 public SDK headers.

A compiler release number (such as Apple Clang 21) is separate from the language standard. CXX passes `-std=c++23` explicitly instead of relying on the compiler's default mode.

The seed build does not need CMake, Ninja, root access, or a package-manager download. TOML parsing and SHA-256 use pinned, vendored libraries. `scripts/build.py` verifies their recorded checksums before compiling.

```sh
git clone https://github.com/ekil1100/cxx.git
cd cxx
python3 scripts/build.py --test
```

This creates `build/cxx`. Keep the source tree: M0 compiles the Build API SDK and Runtime from it when preparing each project. Copying the launcher alone is not a standalone installation.

### First project

From the CXX checkout:

```sh
CXX_BIN="$PWD/build/cxx"
"$CXX_BIN" new /tmp/cxx-hello
cd /tmp/cxx-hello
"$CXX_BIN" build
"$CXX_BIN" run
```

The generated target is named `hello`. A second build scans dependencies but performs no compile or link actions when all tracked inputs and outputs are unchanged.

```sh
"$CXX_BIN" build hello -v
"$CXX_BIN" run hello -- argument1 "argument with spaces"
```

`new` requires a new directory and leaves existing directories untouched. Build commands find `cxxrc.toml` by walking up from the current directory. The program runs with the project root as its working directory.

## Configuration

### Bootstrap

The supported M0 `cxxrc.toml` is:

```toml
[cxx]
edition = "0"

[cpp]
version = "17"

[preset]
name = "llvm"
version = "system"
```

Version fields are strings. Edition defaults to `"0"`, and the user C++ standard defaults to `"17"`. M0 accepts `"17"`, `"20"`, `"23"`, and `"26"`; the chosen compiler must support the requested mode. This setting controls the script and project targets, not CXX's implementation standard: the launcher and Runtime use C++23. Accepting a mode does not imply complete support for every language or library feature.

`preset.name` is required and must be `"llvm"`. Its version may be omitted or set to `"system"`. Other presets, managed versions, extra fields, and configuration-variable expansion produce explicit unsupported-feature errors.

Optional explicit tools:

```toml
[compiler]
path = "/path/to/clang++"

[archiver]
path = "/path/to/llvm-ar"
```

Relative paths are resolved from the project root. Without overrides, CXX looks up `clang++`, then `llvm-ar` (or `ar`) in `PATH`.

Development overrides, from highest to lowest priority:

1. `CXX_BOOTSTRAP_COMPILER` / `CXX_BOOTSTRAP_ARCHIVER` environment variables.
2. The corresponding TOML `path`.
3. The defaults from `PATH`.

`CXX_SDK_ROOT` can point to another CXX source checkout. It overrides the source path embedded in the launcher. Relative SDK paths are resolved from the invocation directory. These variables are M0 development controls, not the proposed V1 `--set` interface.

### Build script

`build.cpp` provides one entry point:

```cpp
#include <cxx/cxx.hpp>

void build(cxx::Build& b) {
    auto& library = b.static_library("greet");
    library.source("src/greet.cpp")
           .include("include", cxx::Visibility::public_)
           .define("GREETING_VERSION=1", cxx::Visibility::public_);

    auto& app = b.executable("hello");
    app.source("src/main.cpp").link(library);
}
```

Use references for targets. The `Build` owns them and keeps their addresses stable. Target names match `[A-Za-z_][A-Za-z0-9_-]*` and must be unique ignoring ASCII case, so `app` and `APP` cannot share a project. This prevents output collisions on case-insensitive filesystems.

- `private_`: applies to the current target.
- `public_`: applies to the current target and its users.
- `interface_`: applies only to users.

`include`, `define`, and `link` default to `private_`. Static libraries retain their required transitive link dependencies even when their compile requirements are private.

A target can override the language mode with `.cpp_standard("20")`. The script itself uses the configured language mode. `b.environment()` exposes the resolved compiler, archiver, C++ standard, and project root.

Build logic can include project headers. Exceptions from the declaration are reported as build errors. Runtime object files are built with the same compiler as the script, using C++23; the script uses its configured standard, starting at C++17. Both are directly linked into the host Driver. The public SDK and Driver entry header remain C++17-compatible; C++23-only types must stay inside the implementation.

### Outputs and incremental builds

Outputs are stored under:

```text
.cxx/driver/                  # Runtime archive and generated Build Driver
.cxx/state/build.lock         # Local process lock; not a resolution cxx.lock
.cxx/build/<target>/          # Executable or static archive
.cxx/build/<target>/.objects/ # Object files and local fingerprints
```

M0 runs a fresh preprocessor scan for each translation unit. Keys include preprocessed content, original source/header contents, paths, compiler content, flags, and a digest of the inherited environment. It compiles the original source to preserve source-location semantics, then checks the inputs again before publishing the result.

This catches newly shadowing headers, `__has_include` changes, and content changes with unchanged timestamps. Inline assembly beyond simple symbol aliases is conservatively recompiled because compiler dependency files can omit assembler file inputs.

Clang's depfiles can rewrite literal backslashes or split control characters in POSIX file paths. CXX checks source/search paths and actual preprocessor line markers, including direct, macro, and transitive includes. Actions with lossy or ambiguous dependency paths bypass incremental reuse and compile the original source each time; `-v` explains why. CXX hashes the primary source directly and avoids trusting lossy header names. Ordinary spaces, quotes, and Unicode paths retain incremental reuse. Archive/link actions can still reuse unchanged object content.

Output content is checked before reuse. Failed compiler, linker, or metadata-publication operations preserve the previous valid artifact. A project lock rejects a second simultaneous build. Commands use argument arrays rather than shell interpolation.

M0 limits:

- This is a **local incremental store**, not the V1 CAS or a portable cache.
- Tool identity currently covers the selected executable, not the full linker/SDK/runtime-library closure. After changing those untracked toolchain components, remove the generated `.cxx/driver/` and `.cxx/build/` directories before rebuilding.
- Build scripts and compilers are trusted native code. There is no sandbox or enforced input isolation.
- Input changes observed during compilation fail the action with a retry message. Concurrent edits that change and revert between checks are outside M0's guarantees. For lossy dependency paths, header changes that leave identical preprocessed text are also outside the before/after consistency check; these actions always rebuild rather than reusing a cached compile result.
- Time-dependent macros can change between scans; M0 may request a retry. Deterministic time inputs are a later capability.
- POSIX execution supports signal forwarding and foreground-terminal handoff for interactive programs. Full M1 process-tree lifecycle and Ctrl-Z job control remain future work; stopped programs currently need an external `SIGCONT`.
- Power-loss durability, full filesystem attack resistance, and multi-file transactional publication remain later work.
- APIs and state formats are experimental Edition 0 and may change.

## Development and verification

```sh
python3 scripts/build.py --test
python3 tests/integration.py --cxx build/cxx
python3 tests/regressions.py --cxx build/cxx
python3 tests/tooling_test.py
python3 scripts/benchmark.py --cxx build/cxx --output build/benchmark.json
```

The tests exercise real Clang builds, static-library visibility and ordering, C++17/20/23 scripts linked with a C++23 Runtime, a standalone C++17 public-header check, graph errors, dependency discovery, source-location behavior, file names with shell/Make-special characters, corrupted outputs, and failed publication. CI runs the same commands on Linux and macOS.

`scripts/build.py --compiler /path/to/clang++ --test` uses that compiler for both the seed build and the real-compiler test suites. The benchmark also accepts `--compiler`; it selects the explicit argument first, then `CXX_BOOTSTRAP_COMPILER`, then `clang++` from `PATH`, and records the selected path and version.

The benchmark records a cold Driver/full build, three no-op builds, a single-source edit, and a shared-header edit. It is an initial wall-time baseline; resource metrics and CMake/Ninja comparison are still planned.

Vendored dependencies and revisions are recorded in [third_party/manifest.json](third_party/manifest.json). Their upstream licenses are retained in their directories.

## Design and progress

- [Product requirements](CXX_PRD_v1.0.md)
- [Architecture proposal](CXX_ARCHITECTURE_v1.0.md)
- [Implementation plan](CXX_IMPLEMENTATION_PLAN_v1.0.md)
- [M0 progress and verification](docs/M0_STATUS.md)
- [Domain vocabulary](CONTEXT.md)
