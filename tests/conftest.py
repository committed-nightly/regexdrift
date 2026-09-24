from __future__ import annotations

import pytest

from regexdrift.engines import BY_NAME


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


def requires(*names: str):
    """Skip unless every named engine is installed.

    Engines are skipped individually rather than the suite refusing to run,
    because the tool's whole posture is that a missing engine is a known
    unknown. What stops that from hollowing out the suite is
    test_engines.py::test_the_baseline_engines_are_present, which fails outright
    if the four engines every Linux box has are not there -- so a CI machine
    cannot quietly skip its way to green.
    """
    import pytest as _pytest

    missing = [n for n in names if not BY_NAME[n].available()]
    return _pytest.mark.skipif(bool(missing), reason=f"not installed: {', '.join(missing)}")
