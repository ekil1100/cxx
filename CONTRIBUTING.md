# Contributing to CXX

CXX is an experimental native C++ build tool. Start with the [README](README.md) for usage and [M0 status](docs/M0_STATUS.md) for implemented capabilities and known limits. The [PRD](CXX_PRD_v1.0.md) is the requirements baseline; planned features are not delivered features.

## Development environment

Use Linux or macOS with:

- Clang and a C++ standard library that support the C++23 features used by CXX: explicit object parameters, `std::ranges::contains`, and `std::string::contains`.
- The platform SDK and linker.
- `llvm-ar` or `ar`.
- Python 3.14 or later for source bootstrap and test orchestration.

The [CI workflow](.github/workflows/ci.yml) selects LLVM 20 from the official LLVM APT repository on Ubuntu 24.04 and Homebrew LLVM 20 on macOS 14. Ubuntu Clang 18.1.3 crashed while generating code for the Runtime's explicit-object-parameter lambdas in CI; use the configured LLVM 20 toolchain. Compiler and standard-library capabilities both matter; accepting `-std=c++23` alone is insufficient. See M0 status for verified environments.

The repository pins Python 3.14 in `.python-version`, and CI uses that version on both platforms. Ensure `python3 --version` reports 3.14 or later before running the commands below. With [uv](https://docs.astral.sh/uv/), set up a local environment with:

```sh
uv python install 3.14
uv venv --python 3.14
source .venv/bin/activate
uv pip install -r requirements-test.txt
```

`requirements-test.txt` pins pytest 9.1.1. With a pip-enabled environment, `python3 -m pip install -r requirements-test.txt` is equivalent. Keep this environment active for `task test` and `task e2e`. Plain bootstrap/build commands need no Python test dependencies.

CMake and Ninja are not required. Third-party sources are vendored; the bootstrap script verifies their checksums before compiling.

## Task shortcuts (optional)

Install [Task v3](https://taskfile.dev/docs/installation) to use `Taskfile.yml`. Task only wraps the commands below; CXX still owns the build graph and incremental decisions. All tasks run from the repository root.

| Command | Purpose |
|---|---|
| `task` | List available tasks |
| `task format` | Format Python sources with Ruff |
| `task lint` | Lint Python sources and check formatting |
| `task bootstrap` | First build, without running tests |
| `task build` | Daily native build |
| `task build -- cxx -v` | Build one target with verbose output |
| `task compdb` | Build targets and generate root `compile_commands.json` for clangd |
| `task unit` | Build and run C++ unit tests |
| `task test` | Source bootstrap, self-build, GoogleTest, and all pytest checks |
| `task e2e -- -k noop` | Selected pytest checks against an existing build |
| `task e2e -- -m tooling` | Tooling checks without a native build |
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
CXX_BOOTSTRAP_COMPILER=/path/to/clang++ ./build/support_test
```

The unit executable uses `CXX_BOOTSTRAP_COMPILER` for compiler-dependent scans and defaults to `clang++` only when the variable is unset. An empty value is an error. `task unit COMPILER=/path/to/clang++` passes the selection to both the build and test execution.

Use `CXX_BOOTSTRAP_ARCHIVER` when an explicit archiver is required. These environment choices affect incremental keys; changing them can cause a rebuild. `--compiler` is an option of the Python tools, not a CXX CLI option.

Python remains responsible for dependency checksum validation and test orchestration. Direct `cxx build` does not perform the manifest check. The seed and formal targets use C++23, `-O2`, `-Wall -Wextra -Wpedantic`, and system includes for third-party headers. In `build.cpp`, these are selected with `.optimization(cxx::Optimization::speed)`, `.warnings()`, and `.system_include(...)`. Optimization and warning settings are target-local; system includes follow usage visibility. These settings cover the current self-build without introducing a full Profile system. Bootstrap-generated Driver/Runtime compilation keeps its existing flags.

If changes break self-building, fix the source and rerun `python3 scripts/build.py` to regenerate the seed. Bootstrap and build files should evolve together when the source layout changes.

### Compilation database

After bootstrap, run `task compdb` to generate the checkout's `compile_commands.json`. Use `task compdb COMPILER=/path/to/clang++` to select a compiler. The helper uses CXX's verbose dependency scans, so it captures current target flags even on a no-op build. It includes declared project targets and excludes bootstrap Runtime/Driver commands. Regenerate it after changing `build.cpp` or compiler settings. The generated file is ignored by Git.

## Python formatting and linting

Use Ruff 0.16.8 for Python formatting and linting. Install it with `uv tool install ruff==0.16.8` or `python3 -m pip install ruff==0.16.8` in your virtual environment. `ruff.toml` sets the Python 3.14 target and pins the Ruff version to keep local and CI results consistent.

```sh
ruff format .
ruff check .
ruff format --check .
```

The equivalent Task commands are `task format` and `task lint`. CI checks lint and formatting without changing files. The lint configuration uses Ruff's default rules (`E4`, `E7`, `E9`, and `F`).

## Tests and verification

For a code change, run the relevant focused checks during development. Before submitting the final code state, run:

```sh
python3 scripts/build.py --test
git diff --check
```

The unified command already runs all suites below; there is no need to repeat them immediately afterward.

| Check | Command | Coverage |
|---|---|---|
| C++ unit tests | `./build/support_test` | GoogleTest cases for hashing, process execution, publication, and SDK ownership |
| Self-hosting | `python3 -m pytest tests/self_host.py --cxx=build/cxx` | A real self-rebuild through the short entrypoint, SDK paths, no-op reuse, and an observable source change |
| Integration | `python3 -m pytest tests/integration.py --cxx=build/cxx` | CLI, graph semantics, language modes, incremental correctness, and failures |
| Regressions | `python3 -m pytest tests/regressions.py --cxx=build/cxx` | Special paths, output collisions, terminal handoff, and signals |
| M1 libraries | `python3 -m pytest tests/m1_libraries.py --cxx=build/cxx` | Shared/object linking, visibility, runtime relocation, and incremental builds |
| M1 generated files | `python3 -m pytest tests/m1_generated.py --cxx=build/cxx` | Generator ordering, cache behavior, output publication, and failure recovery |
| M1 test/install | `python3 -m pytest tests/m1_test_install.py --cxx=build/cxx` | Test execution, installation selection, runtime relocation, and destination safety |
| Tooling | `python3 -m pytest -m tooling` | Compiler propagation, output links, and benchmark metadata |
| P02 prototype | `python3 -m pytest tests/prototypes/p02/run.py --compiler=/path/to/clang++` | Single declaration, deferred binding, and Lock publication |
| P04 runner | `python3 -m pytest tests/prototypes/p04/test_runner.py` | Scan contract and missing-tool diagnostics |

The C++ suite uses pinned GoogleTest v1.18.0, compiled by CXX as a test-only static library. `build.cpp` also registers it as `support`, so `./build/cxx test support` builds and runs it through CXX. The executable provides standard GoogleTest discovery, filtering, and reports:

```sh
./build/support_test --gtest_list_tests
./build/support_test --gtest_filter='FileActionTest.*'
./build/support_test --gtest_output=xml:build/unit-results.xml
```

The custom test main retains subprocess entrypoints used by process regressions; it does not link `gtest_main`. Python end-to-end tests run under pytest; their existing `unittest.TestCase` assertions and setup/cleanup are retained. GoogleMock is not included. See the [vendored dependency notes](third_party/googletest/README.md) for provenance and scope.

Pass `--compiler=/path/to/clang++` to the self-hosting, integration, regression, M1 library, generated-file, and test/install suites when testing a non-default compiler. Build current artifacts before running a suite directly. Add a regression test that exercises the actual failure path when fixing a bug.

For direct pytest commands, always use `--cxx=PATH` and `--compiler=PATH` (quote values containing spaces, for example `--compiler="/path with spaces/clang++"`). The `=` keeps existing executable paths out of pytest's initial root-directory and conftest discovery. Separate path values can select an external root or load another checkout's conftest before these options are registered. The standalone `scripts/build.py` and benchmark argparse interfaces still accept separate values.

The full `--test` suite uses one pytest session and writes `pytest-results.xml` under the selected build output directory. CI uploads this report even when tests fail. Use `-k` for name expressions, `-m e2e`, `-m prototype`, or `-m tooling` for groups, and `--junitxml=build/selected-results.xml` for a focused report. `--collect-only` and tooling-only selection work without a compiler or built CXX. E2E fixtures validate `--cxx` and `--compiler` when selected; P02 compiles its fixture only when its tests are selected. Tests remain sequential; xdist is not configured because some classes share a project directory.

The full `--test` suite includes P02 and the P04 runner checks. The real Modules prototype needs a matching upstream Clang and `clang-scan-deps`, and runs separately (also configured in CI):

```sh
python3 tests/prototypes/p04/run.py --compiler /path/to/clang++ --scanner /path/to/clang-scan-deps
```

These isolated experiments do not add production package or Modules support. See the [P02 notes](tests/prototypes/p02/README.md) and [P04 notes](tests/prototypes/p04/README.md) for evidence and limitations.

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
