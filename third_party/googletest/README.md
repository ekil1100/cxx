# GoogleTest

## Purpose

Official GoogleTest v1.18.0, pinned to commit
`063de7e9578f82b369302001269680b4b1553359` from
https://github.com/google/googletest (SPDX: `BSD-3-Clause`).
The GitHub Git References API resolves `refs/tags/v1.18.0` directly to this commit.

## Installation

The source was copied byte-for-byte from the official immutable archive:
https://codeload.github.com/google/googletest/tar.gz/063de7e9578f82b369302001269680b4b1553359

This subset contains the upstream `LICENSE`, `googletest/include/`, and
`googletest/src/`, except `gtest_main.cc`. GoogleMock, examples, upstream tests,
and build tooling are excluded. The upstream header-tree README is preserved.
`../manifest.json` records SHA-256 hashes for every file in this directory,
including this local README. Only this README is locally authored.

## Configuration

The root `build.cpp` builds `googletest/src/gtest-all.cc` as the `gtest` static
library with C++23 and `-O2 -Wall -Wextra -Wpedantic`. The private include path
is `googletest/`; the public system include path is `googletest/include/`.
No CMake or upstream build tooling is used. Run `python3 scripts/build.py` from
the repository root to bootstrap the native build.

`support_test` links `gtest` and `cxx_support`, supplies its own `main`, and is
registered as the `support` test. The production `cxx` target does not link
GoogleTest. GoogleTest facilities, including pthread support and death tests,
retain their upstream defaults. Thread symbols rely on the project's configured
Linux/macOS toolchain; a platform requiring extra pthread flags must provide
those flags rather than disabling GoogleTest facilities.
