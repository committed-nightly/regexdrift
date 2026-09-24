"""Tests for the adapters.

The expensive mistakes here are all of the same shape: an adapter that returns
"matched nothing" when the truth is something else. That reads as agreement,
agreement is a green tick, and a green tick is the tool being wrong in the
direction nobody checks. So the tests below mostly pin down the difference
between nothing, no, and not installed.
"""

from __future__ import annotations

import os

import pytest
from conftest import requires

from regexdrift.engines import (
    BY_NAME,
    ENGINES,
    SED_DELIMITERS,
    Status,
    _sed_delimiter,
    installed,
    parse_numbers,
    select_engines,
)

BASELINE = ["grep", "grep -E", "sed", "python"]


def test_the_baseline_engines_are_present():
    """grep, sed and python are on every machine this could run on.

    If this fails, the rest of the suite is skipping rather than passing, and a
    green tick would mean nothing at all. This is the guard that stops the
    per-engine skips from being a way to accidentally test zero engines.
    """
    missing = [name for name in BASELINE if not BY_NAME[name].available()]
    assert not missing, f"baseline engines missing: {missing}"


def test_parse_numbers_reads_both_output_shapes():
    # grep and rg prefix the line; awk, sed, python and node print the number alone.
    assert parse_numbers("3:d+\n4:\\d+\n") == {3, 4}
    assert parse_numbers("3\n4\n") == {3, 4}


def test_parse_numbers_is_not_confused_by_a_matched_line_starting_with_a_digit():
    assert parse_numbers("7:123abc\n") == {7}


def test_parse_numbers_ignores_anything_that_is_not_a_record():
    assert parse_numbers("") == frozenset()
    assert parse_numbers("grep: something went wrong\n") == frozenset()


def test_sed_delimiter_avoids_characters_in_the_pattern():
    assert _sed_delimiter("a%b") != "%"
    assert _sed_delimiter("plain") == SED_DELIMITERS[0]


def test_sed_delimiter_gives_up_rather_than_guessing():
    """No delimiter left means sed is unavailable for this pattern, not wrong."""
    assert _sed_delimiter(SED_DELIMITERS) is None


@requires("sed")
def test_sed_is_reported_unavailable_when_no_delimiter_fits(write_lines):
    path = write_lines(["anything"])
    result = BY_NAME["sed"].run(SED_DELIMITERS, path)
    assert result.status is Status.UNAVAILABLE
    assert "delimiter" in result.message


@requires("sed")
def test_sed_handles_a_pattern_containing_a_slash(write_lines):
    path = write_lines(["a/b", "axb"])
    result = BY_NAME["sed"].run("a/b", path)
    assert result.status is Status.ACCEPTED
    assert result.matched == {1}


@requires("grep")
def test_a_pattern_starting_with_a_dash_is_a_pattern(write_lines):
    """Passed positionally, `-v` would be grep's invert flag and match everything."""
    path = write_lines(["-v", "other"])
    result = BY_NAME["grep"].run("-v", path)
    assert result.status is Status.ACCEPTED
    assert result.matched == {1}


@requires("gawk")
def test_awk_gets_the_pattern_unmangled(write_lines):
    r"""`gawk -v p='\d'` rewrites the value before the regex engine sees it.

    Via ENVIRON it does not, so gawk lands with the other POSIX engines on the
    bare letter rather than on whatever -v made of it.
    """
    path = write_lines(["ddd", "123"])
    result = BY_NAME["gawk"].run(r"\d+", path)
    assert result.status is Status.ACCEPTED
    assert result.matched == {1}


@requires("grep")
def test_a_refusal_is_not_a_match_of_nothing(write_lines):
    path = write_lines(["aa"])
    result = BY_NAME["grep"].run(r"a\{", path)
    assert result.status is Status.REJECTED
    assert result.matched == frozenset()
    assert result.message


@requires("grep")
def test_matching_nothing_is_an_answer(write_lines):
    path = write_lines(["aa"])
    result = BY_NAME["grep"].run("zzz", path)
    assert result.status is Status.ACCEPTED
    assert result.matched == frozenset()


def test_a_missing_engine_is_unavailable_and_says_which_binary():
    engine = ENGINES[0].__class__(
        name="nope",
        dialect="imaginary",
        binary="definitely-not-a-real-binary",
        _argv=lambda p, path: ["definitely-not-a-real-binary", p, path],
    )
    result = engine.run("a", "/dev/null")
    assert result.status is Status.UNAVAILABLE
    assert "definitely-not-a-real-binary" in result.message


def test_select_engines_does_not_filter_by_availability():
    """The report has to be able to name what was missing. See select_engines."""
    assert len(select_engines()) == len(ENGINES)
    assert select_engines(["grep"]) == [BY_NAME["grep"]]


def test_installed_is_a_subset_of_selected():
    assert set(e.name for e in installed(select_engines())) <= set(e.name for e in ENGINES)


@pytest.mark.parametrize("name", [e.name for e in ENGINES])
def test_no_adapter_lets_a_pattern_reach_a_shell(name, write_lines, tmp_path):
    """A pattern is data. If any adapter pastes it into a shell, this notices.

    Every engine here is spawned without a shell, but awk and sed both take their
    pattern as part of a program text in the obvious implementation, and the
    obvious implementation is one f-string away from being a command injection in
    a tool whose input is punctuation.
    """
    canary = tmp_path / "canary"
    path = write_lines(["harmless"])
    pattern = f"$(touch {canary}); `touch {canary}`; x"
    result = BY_NAME[name].run(pattern, path)
    assert not canary.exists(), f"{name} executed the pattern"
    assert result.status in (Status.ACCEPTED, Status.REJECTED, Status.UNAVAILABLE)


@pytest.mark.parametrize("name", [e.name for e in ENGINES])
def test_every_adapter_reports_one_of_three_states(name, write_lines):
    path = write_lines(["abc", "def"])
    result = BY_NAME[name].run("abc", path)
    if result.status is Status.ACCEPTED:
        assert result.matched == {1}
    else:
        assert result.matched == frozenset()


def test_awk_program_text_contains_no_interpolation():
    """The program is a constant. Nothing about a pattern can become code."""
    from regexdrift.engines import _AWK_PROGRAM

    assert "REGEXDRIFT_PATTERN" in _AWK_PROGRAM
    assert "{" in _AWK_PROGRAM
    assert os.environ.get("REGEXDRIFT_PATTERN") is None
