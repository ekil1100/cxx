# Contributing to CXX

CXX is an experimental native C++ build tool. Start with the [README](README.md) for usage and [M0 status](docs/M0_STATUS.md) for implemented capabilities and known limits. The [PRD](CXX_PRD_v1.0.md) is the requirements baseline; planned features are not delivered features.

## Development environment

Use Linux or macOS with:

- Clang and a C++ standard library that support the C++23 features used by CXX: explicit object parameters, `std::ranges::contains`, and `std::string::contains`.
- The platform SDK and linker.
- `llvm-ar` or `ar`.
- Python 3.8 or later for source bootstrap and test orchestration.

The [CI workflow](.github/workflows/ci.yml) selects LLVM 20 from the official LLVM APT repository on Ubuntu 24.04 and Homebrew LLVM 20 on macOS 14. Ubuntu Clang 18.1.3 crashed while generating code for the Runtime's explicit-object-parameter lambdas in CI; use the configured LLVM 20 toolchain. Compiler and standard-library capabilities both matter; accepting `-std=c++23` alone is insufficient. See M0 status for verified environments.

CMake and Ninja are not required. Third-party sources are vendored; the bootstrap script verifies their checksums before compiling.

## Task shortcuts (optional)

Install [Task v3](https://taskfile.dev/docs/installation) to use `Taskfile.yml`. Task only wraps the commands below; CXX still owns the build graph and incremental decisions. All tasks run from the repository root.

| Command | Purpose |
|---|---|
| `task` | List available tasks |
| `task bootstrap` | First build, without running tests |
| `task build` | Daily native build |
| `task build -- cxx -v` | Build one target with verbose output |
| `task unit` | Build and run C++ unit tests |
| `task test` | Source bootstrap, self-build, and complete verification |
| `task bench` | Build CXX and write `build/benchmark.json` |

Run `task bootstrap` before `task build`, `task unit`, or `task bench`. Select a compiler with `task bootstrap COMPILER=/path/to/clang++` and use the same `COMPILER` value for later tasks. By default, tasks use `CXX_BOOTSTRAP_COMPILER` from the environment, or `clang++` when it is unset. Task is optional; all direct commands below remain available.

## First build

Run from the repository root:

```sh
python3 scripts/build.py
```

This command:

1. Validates `third_party/manifest.json` checksums.
2. Compiles `build/cxx-seed` directly with Clang.
3. Uses the seed to build the targets in `build.cpp`: `cxx_support`, `cxx`, and `support_test`.
4. Creates relative symbolic links at `build/cxx` and `build/support_test` to the native artifacts under `.cxx/build/`.

This builds the unit-test executable but does not run tests. Add `--test` when you want the full verification described below.

To select a compiler explicitly:

```sh
python3 scripts/build.py --compiler /path/to/clang++
```

The selected compiler is passed to the native self-build and, when `--test` is requested, the real-compiler test suites. Keep the source checkout available: M0 uses its SDK headers and Runtime sources when preparing Build Drivers. The launcher alone is not a standalone installation.

## Daily development

After the first build:

```sh
./build/cxx build
./build/support_test
```

The short entrypoints follow rebuilt artifacts automatically. For a single target:

```sh
./build/cxx build cxx
./build/cxx build support_test
```

Add `-v` to inspect native compiler commands. For a non-default compiler, set the override for direct CXX invocations:

```sh
CXX_BOOTSTRAP_COMPILER=/path/to/clang++ ./build/cxx build
```

Use `CXX_BOOTSTRAP_ARCHIVER` when an explicit archiver is required. These environment choices affect incremental keys; changing them can cause a rebuild. `--compiler` is an option of the Python tools, not a CXX CLI option.

Python remains responsible for dependency checksum validation and test orchestration. Direct `cxx build` does not perform the manifest check. The seed and formal targets use C++23, `-O2`, `-Wall -Wextra -Wpedantic`, and system includes for third-party headers. In `build.cpp`, these are selected with `.optimization(cxx::Optimization::speed)`, `.warnings()`, and `.system_include(...)`. Optimization and warning settings are target-local; system includes follow usage visibility. These settings cover the current self-build without introducing a full Profile system. Bootstrap-generated Driver/Runtime compilation keeps its existing flags.

If changes break self-building, fix the source and rerun `python3 scripts/build.py` to regenerate the seed. Bootstrap and build files should evolve together when the source layout changes.

## Tests and verification

For a code change, run the relevant focused checks during development. Before submitting the final code state, run:

```sh
python3 scripts/build.py --test
git diff --check
```

The unified command already runs all suites below; there is no need to repeat them immediately afterward.

| Check | Command | Coverage |
|---|---|---|
| C++ unit tests | `./build/support_test` | Hashing, process execution, publication, and SDK ownership |
| Self-hosting | `python3 tests/self_host.py --cxx build/cxx` | A real self-rebuild through the short entrypoint, SDK paths, no-op reuse, and an observable source change |
| Integration | `python3 tests/integration.py --cxx build/cxx` | CLI, graph semantics, language modes, incremental correctness, and failures |
| Regressions | `python3 tests/regressions.py --cxx build/cxx` | Special paths, output collisions, terminal handoff, and signals |
| Tooling | `python3 tests/tooling_test.py` | Compiler propagation, output links, and benchmark metadata |

Pass `--compiler /path/to/clang++` to the self-hosting, integration, and regression suites when testing a non-default compiler. Build current artifacts before running a suite directly. Add a regression test that exercises the actual failure path when fixing a bug.

For build-performance changes, record the benchmark:

```sh
python3 scripts/benchmark.py --compiler /path/to/clang++ \
  --cxx build/cxx --output build/benchmark.json
```

Report the platform, compiler, and relevant before/after results. The benchmark covers cold builds, no-op builds, source edits, and shared-header edits; it is not a complete performance certification.

Documentation-only changes need link, command, and whitespace checks rather than a repeated native build. Always distinguish checks that passed from checks that were not run. The CI workflow runs build/tests and the benchmark on both configured platforms; local success does not establish remote CI success.

## Code and design conventions

### Language and API boundaries

- Core and Runtime implementation code uses C++23.
- Public Build API headers and the Driver entry header remain C++17-compatible.
- User scripts and targets support C++17 and newer modes supported by their toolchain. Keep C++23-only implementation types out of public headers.
- Preserve tests that link scripts in different language modes with the C++23 Runtime.

### Changes

- Follow the surrounding code's naming, formatting, and error-handling patterns.
- Prefer the smallest working end-to-end change. Keep refactoring tied to the requested behavior.
- Use existing dependencies first. New dependencies need a clear purpose and an actively maintained upstream.
- Add defensive checks for concrete failure paths, and explain those paths in the change or test.
- Use English for code comments, diagnostics, and test output. Keep documentation in its existing language.
- Keep cache correctness ahead of hit rate. Changes to dependency discovery or action keys need tests that compare incremental results with a fresh or direct compiler build.

Update user-facing documentation when behavior changes. Consult the [architecture](CXX_ARCHITECTURE_v1.0.md), [implementation plan](CXX_IMPLEMENTATION_PLAN_v1.0.md), [domain vocabulary](CONTEXT.md), and [ADRs](docs/adr/) before changing established boundaries. Record unresolved designs as proposed, and discuss scope changes before expanding a milestone.

### Dependencies and generated files

When updating vendored dependencies, update the revision and file checksums in [the manifest](third_party/manifest.json) and retain upstream licenses. Avoid unrelated edits to vendored sources.

Keep `.cxx/`, `build/`, executables, benchmark reports, and Python caches out of commits. Inspect new files as well as tracked diffs for credentials and machine-specific content.

## Commits and pull requests

Use Conventional Commits with a meaningful subject, for example:

```text
feat(build): add native self-hosting targets
fix(process): restore terminal ownership after command failure
docs: add contributor setup instructions
```

Keep changes focused. A pull request should explain:

- The problem and intended behavior.
- The implementation and any relevant trade-offs.
- Tests or reproductions, with the platform and compiler used.
- Remaining limitations and checks not executed.
- Documentation or compatibility changes, if any.

Review the staged diff before committing. Keep unrelated work separate and do not rewrite shared history. Local integration uses squash followed by fast-forward.
