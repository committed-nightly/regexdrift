r"""Tests for the probe generator.

A wrong probe cannot make the tool wrong -- the engines are still run for real on
whatever is generated -- so these tests are not about correctness of meaning.
They are about the generator being *useful*: producing, for each construct the
tool exists to talk about, at least one line that some engines match and others
do not. A generator that emitted nothing but the pattern itself would pass every
"is it correct" test and be worthless.
"""

from __future__ import annotations

import pytest

from regexdrift.probes import MAX_PROBES, generate
from regexdrift.syntax import bracket_end


def test_the_pattern_itself_is_always_the_first_probe():
    """The reading where every engine treats the whole thing as literal text."""
    assert generate(r"\d+")[0] == r"\d+"


def test_class_escapes_get_both_readings():
    probes = generate(r"\d+")
    assert "ddd" in probes, "the POSIX reading, where \\d is the letter d"
    assert "777" in probes, "the PCRE reading, where \\d is a digit"


def test_bare_quantifier_gets_a_literal_reading_and_a_repeated_one():
    probes = generate("a+")
    assert "a+" in probes, "BRE reads the + as a plain character"
    assert "aaa" in probes, "ERE repeats"


def test_optional_gets_a_reading_with_and_without():
    probes = generate("colou?r")
    assert "colour" in probes
    assert "color" in probes


def test_star_gets_a_reading_with_none_of_it():
    probes = generate(r"\s*#")
    assert "#" in probes, "zero repetitions is the case a maximal reading misses"


def test_interval_is_expanded_and_also_left_literal():
    probes = generate("a{2}")
    assert "aa" in probes
    assert "a{2}" in probes


def test_alternation_yields_each_branch():
    probes = generate("cat|dog")
    assert "cat" in probes
    assert "dog" in probes
    assert "cat|dog" in probes, "BRE reads the bar as a character"


def test_alternation_inside_a_group_is_not_a_branch():
    probes = generate("a(b|c)")
    assert "b" not in probes
    assert "c" not in probes


def test_alternation_inside_a_bracket_expression_is_not_a_branch():
    probes = generate("[a|b]x")
    assert "a" not in probes


def test_bre_spelling_of_alternation_is_split_too():
    probes = generate(r"cat\|dog")
    assert "cat" in probes
    assert "dog" in probes


def test_backreference_gets_the_string_it_would_actually_match():
    """`(a)\\1` matches `aa`, and without this probe nothing disagrees."""
    assert "aa" in generate(r"(a)\1")


def test_backreference_to_a_multi_character_group():
    assert "abab" in generate(r"(ab)\1")


def test_bre_group_spelling_is_understood_for_backreferences():
    assert "aa" in generate(r"\(a\)\1")


def test_a_backreference_with_no_such_group_stays_a_digit():
    probes = generate(r"a\1")
    assert "a1" in probes


def test_bracket_expressions_get_a_member_and_the_literal_form():
    probes = generate("[a-z]+")
    assert "aaa" in probes, "a member of the class, repeated"
    assert "[a-z]+" in probes, "the whole thing as literal text"


def test_negated_bracket_expression_gets_something_outside_it():
    probes = generate("[^0-9]")
    assert "x" in probes


def test_posix_character_class_gets_a_member():
    assert any("7" in probe for probe in generate("[[:digit:]]"))


def test_probes_are_unique_and_ordered():
    probes = generate(r"\w+")
    assert len(probes) == len(set(probes))


def test_probes_are_bounded():
    assert len(generate("a" * 200 + "+?*|" * 40)) <= MAX_PROBES


@pytest.mark.parametrize(
    "pattern",
    [
        "",
        "\\",
        "[",
        "[^",
        "(",
        ")",
        "a{",
        "*",
        "?",
        "|",
        "((((",
        r"\\",
        r"\1",
        "[]]",
        "a" * 500,
    ],
)
def test_the_generator_never_raises(pattern):
    """Malformed patterns are the interesting ones; the engines judge validity."""
    probes = generate(pattern)
    assert isinstance(probes, list)


@pytest.mark.parametrize("pattern", [r"\n", r"a\nb", r"\0", "\n", "x\0y"])
def test_no_probe_can_contain_a_newline_or_a_nul(pattern):
    """Every engine here works a line at a time, so such a probe is not a line.

    Keeping one would silently become a different probe than the one intended
    once written to the input file.
    """
    for probe in generate(pattern):
        assert "\n" not in probe
        assert "\0" not in probe


def test_bracket_end_treats_a_leading_bracket_as_a_member():
    assert bracket_end("[]]x", 0) == 3
    assert bracket_end("[^]]x", 0) == 4


def test_bracket_end_handles_a_posix_class():
    assert bracket_end("[[:digit:]]x", 0) == 11


def test_bracket_end_of_an_unterminated_bracket_is_the_whole_rest():
    assert bracket_end("[abc", 0) == 4


def test_zero_width_escapes_contribute_no_character():
    r"""`\bword\b` needs a probe of `word`, or nothing ever disagrees about it."""
    assert "version" in generate(r"\bversion\b")
    assert "bversionb" in generate(r"\bversion\b"), "and the reading where it is a letter"
