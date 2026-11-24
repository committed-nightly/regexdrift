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
    PROBE_PATH,
    PROBE_PATTERN,
    SED_DELIMITERS,
    Readiness,
    Result,
    Status,
    _sed_delimiter,
    first_complaint,
    installed,
    parse_numbers,
    readiness,
    select_engines,
)

BASELINE = ["grep", "grep -E", "sed", "python"]


def test_the_baseline_engines_can_answer():
    """grep, sed and python are on every machine this could run on.

    If this fails, the rest of the suite is skipping rather than passing, and a
    green tick would mean nothing at all. This is the guard that stops the
    per-engine skips from being a way to accidentally test zero engines.

    Readiness rather than `available()`, which is where this used to look: on a
    grep with no PCRE the two answers differ, and the skips now follow readiness
    too, so a check meant to stop them hollowing out the suite has to ask the
    same question they do.

    It doubles as the check on PROBE_PATTERN itself. All four of these engines
    accept one literal character under any build, so a failure here is either a
    box with no grep or a probe that has stopped being neutral -- and the second
    would make every unusable verdict in the tool wrong. The matching claim
    about all ten engines belongs in CI, which controls what is installed; here
    it would fail on any Mac, where BSD grep has no -P and the right answer is
    to skip those tests rather than to fail.
    """
    unready = [(name, BY_NAME[name].readiness()) for name in BASELINE]
    unready = [row for row in unready if row[1][0] is not Readiness.READY]
    assert unready == [], f"baseline engines cannot answer: {unready}"


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


# --- can this engine answer at all -------------------------------------------
#
# The bug these pin down: `shutil.which` answers for a binary, and `grep -E`,
# `grep -P` and `sed -E` are modes of one. A mode that is not there says so by
# exiting 2, which this adapter reads as a refusal, so PCRE2 being absent was
# reported as PCRE2 refusing the pattern -- agreement among the rest, and the
# dialect the tool exists to contrast against POSIX silently out of the room.


def test_a_mode_the_binary_does_not_have_is_unavailable_not_a_refusal(grep_without_pcre, write_lines):
    """The whole point. grep is installed; PCRE2 is not."""
    path = write_lines(["a1", "bb"])
    result = BY_NAME["grep -P"].run(r"\d", path)
    assert result.status is Status.UNAVAILABLE, f"still filed as {result.status}: {result.message}"
    assert "grep" in result.message
    assert "-P" in result.message


def test_the_other_modes_of_that_same_binary_still_answer(grep_without_pcre, write_lines):
    """A missing mode must not take the working ones with it.

    Without this, "report the engine as unavailable" could be satisfied by
    giving up on grep entirely, which would lose two engines to fix one.
    """
    path = write_lines(["a1", "bb", "cc3"])
    for name in ("grep", "grep -E"):
        result = BY_NAME[name].run("[0-9]", path)
        assert result.status is Status.ACCEPTED, f"{name}: {result.message}"
        assert result.matched == {1, 3}


def test_readiness_calls_that_mode_unusable_and_the_binary_present(grep_without_pcre):
    states = {engine.name: state for engine, state, _ in readiness()}
    assert states["grep -P"] is Readiness.UNUSABLE
    assert states["grep"] is Readiness.READY
    assert states["grep -E"] is Readiness.READY


@requires("grep -P")
def test_a_real_refusal_is_not_turned_into_unavailability(write_lines):
    """The control, and the one that would catch an over-eager probe.

    A working grep -P handed a pattern it cannot compile has refused the
    pattern. If the probe swallowed this into UNAVAILABLE, every genuine
    portability finding in the tool would quietly become "engine not run".
    """
    path = write_lines(["a1"])
    for pattern in ("(", "a{2,1}"):
        result = BY_NAME["grep -P"].run(pattern, path)
        assert result.status is Status.REJECTED, f"{pattern!r} came back {result.status}"
        assert result.message


@requires("python")
def test_a_python_refusal_says_what_re_objected_to(write_lines):
    """A refusal has to carry the engine's reason, not the shape of its failure.

    The adapter runs `python -c` and reads back stderr, so an uncaught
    `re.error` made the first line of the refusal "Traceback (most recent call
    last):" -- a true sentence about the subprocess and no help at all about the
    pattern. The status was never wrong, which is why it survived: the report
    had a `refused` row for python with a reason that could not be acted on,
    sitting directly under grep -P naming its own objection to the same pattern.
    """
    path = write_lines(["cat"])
    result = BY_NAME["python"].run(r"\ycat\y", path)
    assert result.status is Status.REJECTED
    assert "bad escape" in result.message, result.message
    assert not result.message.startswith("Traceback")
    # What the report actually prints, which is the line that was wrong.
    assert "Traceback" not in first_complaint(BY_NAME["python"].binary, result.message)


@requires("node")
def test_a_node_refusal_says_what_the_regexp_engine_objected_to(write_lines):
    """The same bug as python's, and it hid behind being louder rather than quieter.

    python's uncaught `re.error` put "Traceback (most recent call last):" on the
    first line. node's uncaught throw puts the *location* there -- `[eval]:1` --
    and the report prints the first line, so the refused row read

        refused   node: [eval]:1

    which names neither the construct nor the fact that a regex was involved.
    One of nine engines whose answer was a column width's worth of nothing.

    Two things are asserted separately because they failed for different
    reasons. The reason has to be there, and `[eval]` and the stack frames have
    to not be -- the sixteen-line report quoted this program back in full on
    line two, so `refused node:` was a channel for regexdrift's own source.
    """
    path = write_lines(["aa"])
    result = BY_NAME["node"].run("(a", path)
    assert result.status is Status.REJECTED
    assert "Unterminated group" in result.message, result.message
    assert "[eval]" not in result.message, result.message
    assert "require('fs')" not in result.message, result.message
    assert "\n" not in result.message.strip(), f"still multi-line: {result.message!r}"
    # What the report prints, which is the line that was wrong.
    visible = first_complaint(BY_NAME["node"].binary, result.message)
    assert "Unterminated group" in visible, visible


@requires("node")
def test_node_still_answers_normally_after_the_guard(write_lines):
    """The guard is in the same program text that does the matching.

    A `try` that caught the compile and then fell through to `re.test` with an
    undefined `re` would turn every *accepted* pattern into a refusal, and the
    refusal tests above would all still pass. So the accepting path is pinned
    here, including stderr staying empty: node's silence is what makes gawk the
    only engine in the README's example that warns.
    """
    path = write_lines(["a", "b", "a"])
    result = BY_NAME["node"].run("a", path)
    assert result.status is Status.ACCEPTED, result.message
    assert result.matched == {1, 3}
    assert result.warning == ""


def test_readiness_names_the_binary_it_could_not_find(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda binary: None)
    state, detail = BY_NAME["rg"].readiness()
    assert state is Readiness.MISSING
    assert "rg" in detail


def test_a_timeout_stays_a_refusal_and_is_not_re_asked(monkeypatch, write_lines):
    """A pattern that backtracks forever is about the pattern, not the engine.

    Asserted by making the second call -- the probe -- answer differently from
    the first. If `run` re-asked after a timeout it would read the second answer
    and report UNAVAILABLE, so this fails if the timed_out flag stops working.
    """
    import subprocess as sp

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            raise sp.TimeoutExpired(argv, 10)
        raise AssertionError("the probe ran after a timeout")

    monkeypatch.setattr("regexdrift.engines.subprocess.run", fake_run)
    result = BY_NAME["grep"].run("(a+)+b", write_lines(["a" * 40]))
    assert result.status is Status.REJECTED
    assert result.timed_out is True
    assert len(calls) == 1


def test_probe_path_is_readable_and_empty():
    """os.devnull, not the caller's input: no temp file, no read, always there."""
    assert os.path.exists(PROBE_PATH)
    with open(PROBE_PATH, encoding="utf-8") as handle:
        assert handle.read() == ""


def test_the_probe_pattern_has_no_syntax_to_disagree_about():
    """One alphanumeric character, which is a literal in all ten dialects.

    Every `unusable` verdict in the tool rests on this. A probe with any
    metacharacter in it -- `\\w`, `a+`, `.` -- is a pattern the dialects can
    legitimately differ on, and then an engine refusing it would mean nothing
    about whether the engine works. Checked here rather than left to the comment
    beside the constant, because improving the probe is an obvious thing to try.
    """
    assert len(PROBE_PATTERN) == 1
    assert PROBE_PATTERN.isalnum()


def test_first_complaint_drops_the_binarys_own_prefix():
    assert first_complaint("grep", "grep: invalid option -- 'P'") == "invalid option -- 'P'"
    assert first_complaint("grep", "grep: one\ngrep: two") == "one"
    assert first_complaint("sed", "something else entirely") == "something else entirely"
    assert first_complaint("sed", "") == ""


def test_readiness_covers_every_engine_exactly_once():
    rows = readiness()
    assert [engine.name for engine, _, _ in rows] == [engine.name for engine in ENGINES]


def test_a_ready_engine_reports_no_reason_and_an_unready_one_always_does():
    """The report prints `detail` only when it is not READY, so it must be there."""
    for engine, state, detail in readiness():
        if state is Readiness.READY:
            assert detail == "", f"{engine.name} is ready and still explaining itself"
        else:
            assert detail, f"{engine.name} is {state.value} with no reason given"


def test_result_defaults_to_not_timed_out():
    assert Result("x", Status.ACCEPTED).timed_out is False
