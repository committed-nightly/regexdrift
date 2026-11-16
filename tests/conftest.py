from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from regexdrift.engines import BY_NAME, Readiness

SHIM_ROOT = Path(__file__).parent / "shims"


def pytest_configure(config):
    config.addinivalue_line("markers", "engine(name): needs that engine installed")


@pytest.fixture
def write_lines(tmp_path):
    """Write lines to a file and return its path, the way the CLI does."""

    def write(lines: list[str], name: str = "input.txt") -> str:
        path = tmp_path / name
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return str(path)

    return write


@lru_cache(maxsize=None)
def _can_answer(name: str) -> str:
    """Whether this engine can answer here, as a reason or "" for yes.

    Cached, because it costs a subprocess and the `requires` decorators ask
    about the same ten engines across twenty-odd call sites at import time.
    """
    state, detail = BY_NAME[name].readiness()
    return "" if state is Readiness.READY else f"{name} is {state.value}"


def requires(*names: str):
    """Skip unless every named engine can actually answer.

    Engines are skipped individually rather than the suite refusing to run,
    because the tool's whole posture is that a missing engine is a known
    unknown. What stops that from hollowing out the suite is
    test_engines.py::test_the_baseline_engines_are_present, which fails outright
    if the four engines every Linux box has are not there -- so a CI machine
    cannot quietly skip its way to green.

    This asked `available()` until the readiness work, which is a PATH check,
    and so could not skip a test on a grep whose `-P` does not exist: the test
    ran and failed with a portability finding that was really a missing engine.
    The skip and the tool now draw the line in the same place.
    """
    import pytest as _pytest

    unanswerable = [reason for reason in (_can_answer(n) for n in names) if reason]
    return _pytest.mark.skipif(
        bool(unanswerable), reason="cannot answer here: " + ", ".join(unanswerable)
    )


@pytest.fixture
def grep_without_pcre(monkeypatch):
    """Put a grep with no PCRE first on PATH, and leave everything else alone.

    This is the state `shutil.which` cannot see and the one the tool used to
    misreport: the binary is installed, three engines run through it, and one of
    its modes does not exist. Real on busybox, on BSD, and on any GNU grep built
    --disable-perl-regexp.

    The real grep is handed to the shim by name rather than found on PATH,
    because the shim is about to *be* the grep on PATH and would otherwise
    re-exec itself.
    """
    shim = SHIM_ROOT / "grep-without-pcre"
    if not os.access(shim / "grep", os.X_OK):
        pytest.skip(f"{shim / 'grep'} is not executable")
    # The shim directory is excluded when resolving the real grep, because the
    # shim may already be on PATH -- running the suite under one was how this was
    # found, and a shim that execs itself recurses until the 10s timeout, ten
    # times over, and reports plain `grep` as unusable.
    elsewhere = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and Path(entry).resolve() != shim.resolve()
    )
    real = shutil.which("grep", path=elsewhere)
    if real is None:
        pytest.skip("no grep to wrap")
    monkeypatch.setenv("SHIM_REAL_GREP", real)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    return shim
