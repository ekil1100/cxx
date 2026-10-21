"""Shared pytest options and lazy setup for real-compiler suites."""

import os
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parent


def pytest_addoption(parser):
    parser.addoption("--cxx", default=str(ROOT / "build/cxx"))
    parser.addoption(
        "--compiler", default=os.environ.get("CXX_BOOTSTRAP_COMPILER", "clang++")
    )


def resolve_compiler(name):
    """Keep the invocation name and symlink/.. semantics after changing cwd."""
    executable = shutil.which(name)
    if executable is None:
        raise ValueError("Compiler not found: " + name)
    return str(Path(executable).absolute())


@pytest.fixture(scope="module")
def compiler(request):
    try:
        return resolve_compiler(request.config.getoption("--compiler"))
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)


@pytest.fixture(scope="module", autouse=True)
def configure_e2e(request):
    # Module setup runs before unittest's class-scoped setUpClass adapter.
    if request.node.get_closest_marker("e2e") is None:
        return
    executable = Path(request.config.getoption("--cxx")).absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        pytest.fail(
            "CXX executable not found or not executable: " + str(executable),
            pytrace=False,
        )
    request.module.CXX = str(executable)
    request.module.COMPILER = request.getfixturevalue("compiler")
