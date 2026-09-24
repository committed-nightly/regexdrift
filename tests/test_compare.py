"""Tests for grouping and for the three states never being conflated."""

from __future__ import annotations

from regexdrift.compare import compare, group_results
from regexdrift.engines import Result, Status
from regexdrift.notes import explain


def accepted(engine: str, *lines: int) -> Result:
    return Result(engine, Status.ACCEPTED, frozenset(lines))


def rejected(engine: str, message: str = "no") -> Result:
    return Result(engine, Status.REJECTED, message=message)


def unavailable(engine: str) -> Result:
    return Result(engine, Status.UNAVAILABLE, message="not on PATH")


def test_engines_matching_the_same_lines_are_one_group():
    groups = group_results([accepted("a", 1, 2), accepted("b", 2, 1)])
    assert len(groups) == 1
    assert groups[0].engines == ("a", "b")


def test_engines_matching_different_lines_are_different_groups():
    groups = group_results([accepted("a", 1), accepted("b", 2)])
    assert len(groups) == 2


def test_groups_keep_the_order_the_engines_ran_in():
    """So the report is stable between runs rather than however a set hashed."""
    groups = group_results([accepted("a", 5), accepted("b", 1), accepted("c", 5)])
    assert [g.engines for g in groups] == [("a", "c"), ("b",)]


def test_a_refusal_does_not_make_a_group():
    comparison = compare("p", ["x"], [accepted("a", 1), rejected("b")])
    assert not comparison.drifted
    assert comparison.rejected[0].engine == "b"


def test_a_missing_engine_does_not_make_a_group_and_is_still_named():
    comparison = compare("p", ["x"], [accepted("a", 1), unavailable("rg")])
    assert not comparison.drifted
    assert [r.engine for r in comparison.unavailable] == ["rg"]


def test_matching_nothing_is_a_behaviour_that_can_disagree():
    """The dangerous outcome: one engine silently never matches."""
    comparison = compare("p", ["x"], [accepted("a", 1), accepted("b")])
    assert comparison.drifted
    assert len(comparison.groups) == 2


def test_one_engine_alone_is_not_drift():
    assert not compare("p", ["x"], [accepted("a", 1)]).drifted


def test_disputed_lines_exclude_the_ones_everybody_agrees_about():
    comparison = compare(
        "p",
        ["one", "two", "three"],
        [accepted("a", 1, 2), accepted("b", 1, 3)],
    )
    assert comparison.distinguishing == (2, 3)


def test_there_are_no_disputed_lines_without_drift():
    assert compare("p", ["x"], [accepted("a", 1), accepted("b", 1)]).distinguishing == ()


def test_lines_are_addressed_by_one_based_number():
    comparison = compare("p", ["first", "second"], [accepted("a", 2)])
    assert comparison.line(2) == "second"


def test_a_warning_is_recorded_without_changing_the_group():
    warned = Result("gawk", Status.ACCEPTED, frozenset({1}), warning="warning: something")
    comparison = compare("p", ["x"], [accepted("grep", 1), warned])
    assert not comparison.drifted
    assert [r.engine for r in comparison.warned] == ["gawk"]


def test_notes_fire_only_for_constructs_the_pattern_contains():
    slugs = {note.slug for note in explain(r"\d+")}
    assert "class-escape" in slugs
    assert "bre-quantifier" in slugs
    assert "backreference" not in slugs


def test_a_quantifier_inside_a_bracket_expression_is_not_a_quantifier():
    assert "bre-quantifier" not in {note.slug for note in explain("[+?]")}


def test_an_escaped_metacharacter_is_not_a_bare_one():
    slugs = {note.slug for note in explain(r"a\+b\|c\(d")}
    assert "bre-quantifier" not in slugs
    assert "alternation" not in slugs
    assert "group" not in slugs


def test_an_escaped_backslash_before_d_is_not_a_class_escape():
    assert "class-escape" not in {note.slug for note in explain(r"\\d")}


def test_lazy_quantifier_is_detected():
    assert "lazy-quantifier" in {note.slug for note in explain("a+?b")}


def test_a_lone_question_mark_is_not_a_lazy_quantifier():
    assert "lazy-quantifier" not in {note.slug for note in explain("a?b")}


def test_extended_group_is_detected():
    assert "extended-group" in {note.slug for note in explain("(?:ab)")}


def test_a_plain_pattern_has_nothing_to_explain():
    assert explain("hello") == []
