"""Tests for the command line, including the exit codes CI would rely on.

These run the real engines. That is the point: the tool's claim is "I ran them",
and a test suite that mocked the engines would be checking the claim it is least
interesting to check.
"""

from __future__ import annotations

import json

import pytest
from conftest import requires

from regexdrift.cli import EXIT_DRIFT, EXIT_ERROR, EXIT_OK, main


@requires("grep", "grep -E")
def test_the_headline_case_is_drift(capsys):
    r"""`\d+` means two different things and nothing says so."""
    assert main([r"\d+"]) == EXIT_DRIFT
    out = capsys.readouterr().out
    assert "behaviours" in out
    assert "POSIX BRE and ERE have no \\d" in out


@requires("grep", "grep -E")
def test_a_portable_pattern_is_quiet(capsys):
    assert main(["hello"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "matched the same lines" in out
    assert "why they might differ" not in out


@requires("grep", "grep -E")
def test_agreement_on_probes_is_not_claimed_as_agreement(capsys):
    """Probes are guesses, so a 0 has to be scoped to what was actually tried."""
    main(["hello"])
    out = capsys.readouterr().out
    assert "probes" in out


@requires("grep")
def test_a_real_file_is_used_when_given(capsys, write_lines):
    path = write_lines(["alpha", "beta", "gamma"])
    assert main(["beta", path]) == EXIT_OK
    out = capsys.readouterr().out
    assert "3 lines" in out
    assert "generated probes" not in out


@requires("grep")
def test_line_numbers_are_shown_for_a_real_file(capsys, write_lines):
    path = write_lines(["no", "yes"])
    main(["yes", path])
    assert "2:" in capsys.readouterr().out


@requires("grep", "grep -E")
def test_drift_in_a_real_file_names_the_disputed_lines(capsys, write_lines):
    # No `d` and no digit anywhere in the third line, or it is disputed too --
    # ERE reads the pattern as `d+`, and "unrelated" has a d in it.
    path = write_lines(["123", "ddd", "xyz"])
    assert main([r"\d+", path]) == EXIT_DRIFT
    out = capsys.readouterr().out
    assert "xyz" not in out, "a line every engine agrees about is not the finding"


@requires("grep")
def test_reading_stdin_needs_a_dash(monkeypatch, capsys):
    """And never happens by guessing: guessing hung for two minutes. See read_lines."""
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("alpha\nbeta\n"))
    assert main(["alpha", "-"]) == EXIT_OK
    assert "2 lines" in capsys.readouterr().out


@requires("grep")
def test_a_missing_file_is_an_error_not_a_pass(capsys):
    assert main(["a", "/definitely/not/here"]) == EXIT_ERROR
    assert "regexdrift:" in capsys.readouterr().err


@requires("grep")
def test_empty_input_is_an_error_not_a_pass(capsys, write_lines):
    assert main(["a", write_lines([])]) == EXIT_ERROR
    assert "nothing was compared" in capsys.readouterr().err


@requires("grep")
def test_one_engine_cannot_agree_with_itself(capsys):
    """Fewer than two answers is exit 2, not a green tick. See cli's docstring."""
    assert main(["hello", "--engine", "grep"]) == EXIT_ERROR
    assert "not enough to compare" in capsys.readouterr().err


@requires("grep", "grep -E")
def test_two_engines_that_agree_are_enough(capsys):
    assert main(["hello", "--engine", "grep", "--engine", "grep -E"]) == EXIT_OK


@requires("grep", "gawk")
def test_strict_fails_on_a_warning(capsys):
    r"""gawk warns about `\d`; grep does not. Only --strict cares."""
    assert main([r"\dx", "--engine", "gawk", "--engine", "grep", "--strict"]) == EXIT_DRIFT
    assert "warned" in capsys.readouterr().out


@requires("grep", "gawk")
def test_a_warning_alone_is_not_a_failure_by_default():
    assert main([r"\dx", "--engine", "gawk", "--engine", "grep"]) == EXIT_OK


@requires("grep", "grep -E")
def test_a_refusal_is_reported_but_does_not_fail_by_default(capsys):
    """A loud failure is not the kind this tool is for. grep BRE refuses `\\1`."""
    code = main([r"(a)\1", "--engine", "grep", "--engine", "grep -E", "--engine", "python"])
    out = capsys.readouterr().out
    assert "refused" in out
    assert code in (EXIT_OK, EXIT_DRIFT)


@requires("grep", "grep -E")
def test_strict_fails_on_a_refusal():
    assert (
        main([r"(a)\1", "--engine", "grep", "--engine", "grep -E", "--engine", "python", "--strict"])
        == EXIT_DRIFT
    )


def test_uninstalled_engines_are_named_in_the_output(capsys, monkeypatch):
    """The failure this whole shop is about: a check that compared four of ten."""
    monkeypatch.setattr("shutil.which", lambda binary: None if binary == "sed" else "/usr/bin/x")
    main(["hello", "--engine", "grep", "--engine", "grep -E", "--engine", "sed"])
    assert "sed" in capsys.readouterr().out


def test_no_engines_installed_is_an_error(capsys, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda binary: None)
    assert main(["hello"]) == EXIT_ERROR
    assert "nothing was compared" in capsys.readouterr().err


@requires("grep", "grep -E")
def test_json_output_is_valid_and_carries_the_finding(capsys):
    assert main([r"\d+", "--json"]) == EXIT_DRIFT
    payload = json.loads(capsys.readouterr().out)
    assert payload["pattern"] == r"\d+"
    assert payload["drift"] is True
    assert payload["input"]["generated"] is True
    assert len(payload["groups"]) > 1
    assert payload["notes"]


@requires("grep", "grep -E")
def test_json_has_no_notes_without_drift(capsys):
    """Notes are commentary on a measurement, never a lint rule. See notes.py."""
    main(["hello", "--json"])
    assert json.loads(capsys.readouterr().out)["notes"] == []


def test_list_engines_says_which_are_installed(capsys):
    assert main(["--list-engines"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "grep" in out and "rg" in out
    assert "installed" in out or "missing" in out


def test_a_pattern_is_required(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2


def test_an_unknown_engine_is_rejected():
    with pytest.raises(SystemExit):
        main(["a", "--engine", "not-an-engine"])


@requires("grep")
def test_the_scratch_file_is_cleaned_up(write_lines, tmp_path, monkeypatch):
    import os
    import tempfile

    made: list[str] = []
    real = tempfile.mkstemp

    def spy(*args, **kwargs):
        handle, path = real(*args, **kwargs)
        made.append(path)
        return handle, path

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    main(["hello"])
    assert made and not any(os.path.exists(path) for path in made)


@requires("gawk")
def test_an_engine_warning_does_not_leak_the_scratch_path(capsys):
    """gawk names the input file, which is different on every run."""
    main([r"\dx", "--engine", "gawk", "--engine", "grep"])
    out = capsys.readouterr().out
    assert "regexdrift-" not in out
    assert "<input>" in out


@requires("grep", "gawk")
def test_word_boundary_drift_is_found(capsys):
    r"""In awk `\b` is a backspace, so a grep pattern ported there matches nothing.

    gawk spells the word boundary `\y`; mawk has none. Nothing warns, because a
    backspace is a perfectly good escape as far as awk is concerned.
    """
    assert main([r"\bversion\b", "--engine", "grep", "--engine", "gawk"]) == EXIT_DRIFT
    assert "word boundaries" in capsys.readouterr().out
