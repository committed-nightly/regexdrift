r"""What each dialect actually does with a pattern the others read differently.

Nothing else in the suite asked this. The adapters are tested for the difference
between nothing, no and not installed (`test_engines.py`), and the report is
tested for how it groups whatever answers arrive (`test_compare.py`,
`test_cli.py`) -- but the answers themselves came from whichever engines
happened to be on the box, and four of the ten had no test naming them at all:
`grep -P`, `sed -E`, `mawk` and `node`. `rg` had three mentions and all three
were about it being absent.

Measured cost of that, on 37f51c5: with `tests/shims/grep-without-pcre` first on
PATH -- a grep with no PCRE whatsoever -- 140 of 141 tests passed, and the one
that skipped was about readiness rather than about what PCRE2 does with a
pattern. The engine in this repo's own topics could be wholly missing and the
suite still reported success. That is how committed-nightly/regexdrift#2's
defect survived two months: there was nothing here to go red. (logbook#74)

So: a table of patterns, with the line numbers every named engine really returns
for each. Every expectation below was produced by running the engine and reading
it back, never by reading a standard -- the same rule the tool itself follows,
and for the same reason. If an engine changes its mind about one of these, this
goes red naming the engine and the pattern, which is a thing anyone using the
tool would want to know.

Cases are gated all-or-nothing on every engine they name, following
`conftest.requires`: on a box missing one of them a case skips rather than
testing less and passing. Nothing skips in CI, where the job asserts 10 of 10
engines ready before pytest runs.

Which means these cases do not make the shim run above go red, and are not
meant to: under that grep the suite is now 144 passed, 5 skipped, where before
it was 140 passed, 1 skipped. What changed is that the skip list now names four
dialect cases wanting `grep -P`, so a PCRE-less box says out loud that PCRE2
went untested instead of there being nothing to say. Turning that into a
failure is CI's readiness gate, not this file's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from conftest import requires

from regexdrift.engines import BY_NAME, ENGINES, Status, first_complaint

# An expectation of REFUSED is "this engine said no, and the wording is its own
# business". Used where the text is an error string that could reasonably be
# reworded between versions. Where the wording *is* the claim -- ripgrep on
# backreferences, which the README quotes as one of the tool's three opinions --
# the substring is spelled out instead, so rewording it goes red here.
REFUSED = ""


@dataclass(frozen=True)
class Case:
    """One pattern, one corpus, and what each named engine really answers.

    `expect` maps an engine name to either the set of line numbers it matched --
    ACCEPTED, so an empty set means "compiled the pattern and matched nothing",
    which is a different claim from a refusal and the distinction this whole
    repo turns on -- or to a string, meaning REJECTED with that substring in the
    reason the report prints, which is `first_complaint`: the engine's first
    line with its own name stripped off.

    The first line and not the whole of stderr, because "bad escape" appears in
    an uncaught Python traceback as well as in a legible refusal, so a substring
    match over the full message passed both before and after the fix that made
    python's refusal readable. A whole thing matched by a substring is how an
    assertion stops asserting its own sentence (logbook#63), and that is no
    better in a test than it is in a CI script.

    `detail` is for a substring that must appear anywhere in the full complaint.
    Only ripgrep needs it: its first line is the generic "regex parse error:"
    and the construct it is actually refusing is named four lines further down,
    so the thing worth pinning is not the thing the report shows.

    `warns` is checked against every engine in `expect`, not just the ones named
    in it: an engine absent from `warns` is asserted to have said nothing on
    stderr. Warnings feed `--strict`, so "only gawk warns" is a claim with an
    exit code attached to it.
    """

    name: str
    pattern: str
    lines: list[str]
    expect: dict[str, set[int] | str]
    why: str
    warns: dict[str, str] = field(default_factory=dict)
    detail: dict[str, str] = field(default_factory=dict)


CASES = [
    Case(
        name="backslash-d",
        pattern=r"\d",
        lines=["7", "d", "x"],
        expect={
            "grep": {2},
            "grep -E": {2},
            "sed": {2},
            "sed -E": {2},
            "gawk": {2},
            "mawk": {2},
            "grep -P": {1},
            "python": {1},
            "node": {1},
            "rg": {1},
        },
        warns={"gawk": "not a known regexp operator"},
        why=(
            "the README's worked example and the three groups it prints. \\d is a "
            "digit in PCRE2, Python, ECMAScript and Rust's regex; GNU grep, sed "
            "and awk added \\w and \\s and never added \\d, so there it is the "
            "bare letter d. Six engines read this pattern as a different pattern "
            "and all ten exit 0"
        ),
    ),
    Case(
        name="plus-bre-vs-ere",
        pattern="a+b",
        lines=["a+b", "aab", "ab"],
        expect={"grep": {1}, "sed": {1}, "grep -E": {2, 3}, "sed -E": {2, 3}},
        why=(
            "+ is a quantifier in ERE and an ordinary character in BRE, so the "
            "two dialects match disjoint sets of these three lines -- there is no "
            "line both agree on. sed -E's first test: it is the ERE column's "
            "second member and nothing had ever asked it anything"
        ),
    ),
    Case(
        name="gnu-word-boundary",
        pattern=r"\ycat\y",
        lines=["cat", "concat", "cats"],
        expect={
            "gawk": {1},
            "mawk": set(),
            "grep -P": REFUSED,
            "python": "bad escape",
            "rg": "regex parse error",
        },
        detail={"rg": "unrecognized escape sequence"},
        why=(
            "\\y is gawk's word boundary, a GNU extension nothing else here has. "
            "mawk is the point: it accepts the pattern and matches nothing, so it "
            "lands in neither the refused column nor gawk's group, and a report "
            "that folded 'matched nothing' into 'refused' would lose it. \\b "
            "would not do as the pattern -- in awk that is a backspace, so both "
            "awks are quietly fine with it"
        ),
    ),
    Case(
        name="unset-backreference",
        pattern=r"(a)?b\1",
        lines=["b", "aba"],
        expect={
            "node": {1, 2},
            "python": {2},
            "grep -P": {2},
            "grep -E": {2},
            "sed -E": {2},
            "grep": REFUSED,
            "sed": REFUSED,
            "rg": "regex parse error",
        },
        detail={"rg": "backreferences are not supported"},
        why=(
            "ECMAScript matches a backreference to a group that did not "
            "participate as the empty string; PCRE2 and Python fail it. So node "
            "matches 'b' and the other two do not, on a pattern all three "
            "compiled without a word of complaint -- the silent kind of drift "
            "the tool is named for. node's first test, and the only place "
            "ripgrep's headline refusal is pinned to the words it uses"
        ),
    ),
    Case(
        name="lookahead",
        pattern="(?=a)a",
        lines=["a", "b"],
        expect={"grep -P": {1}, "python": {1}, "node": {1}, "rg": "regex parse error"},
        detail={"rg": "look-around, including look-ahead and look-behind, is not supported"},
        why=(
            "the other half of what Rust's regex will not do. PCRE2, Python and "
            "ECMAScript all take look-around; the RE2 family refuses it outright "
            "and says which construct, which is the loud kind of difference and "
            "the one the README promises you will hear about"
        ),
    ),
]


@pytest.mark.parametrize(
    "case", [pytest.param(c, id=c.name, marks=requires(*c.expect)) for c in CASES]
)
def test_each_engine_answers_this_pattern_the_way_it_really_does(case, write_lines):
    path = write_lines(case.lines)
    for name, expected in case.expect.items():
        result = BY_NAME[name].run(case.pattern, path)

        if isinstance(expected, str):
            assert result.status is Status.REJECTED, (
                f"{name} was expected to refuse {case.pattern!r}, got "
                f"{result.status.value} matching {sorted(result.matched)}. {case.why}"
            )
            visible = first_complaint(BY_NAME[name].binary, result.message)
            assert expected in visible, (
                f"{name} refused {case.pattern!r}, and the reason the report "
                f"prints is {visible!r}, not the pinned {expected!r}"
            )
            wanted_detail = case.detail.get(name)
            if wanted_detail is not None:
                assert wanted_detail in result.message, (
                    f"{name} refused {case.pattern!r} but never said "
                    f"{wanted_detail!r}: {result.message!r}"
                )
        else:
            assert result.status is Status.ACCEPTED, (
                f"{name} did not answer about {case.pattern!r}: {result.message}"
            )
            assert result.matched == expected, (
                f"{name} matched lines {sorted(result.matched)} of {case.lines} "
                f"for {case.pattern!r}, expected {sorted(expected)}. {case.why}"
            )

        wanted = case.warns.get(name)
        if wanted is None:
            assert not result.warning, f"{name} warned unexpectedly: {result.warning!r}"
        else:
            assert wanted in result.warning, (
                f"{name} was expected to warn about {wanted!r} and said "
                f"{result.warning!r}"
            )


def test_every_engine_has_a_dialect_case():
    """The guard on logbook#74 reopening.

    Add an engine to ENGINES and this goes red until a case names it. It reads
    the table and runs nothing, so unlike the cases above it cannot skip its way
    to green on a box that is missing half the engines -- which is the failure
    it exists to stop.
    """
    named = {name for case in CASES for name in case.expect}
    assert sorted({e.name for e in ENGINES} - named) == []


def _as_key(expected):
    """A hashable form of an expectation, so two can be compared for sameness."""
    return frozenset(expected) if isinstance(expected, set) else expected


def test_every_case_discriminates_between_the_engines_it_names():
    """A case whose named engines all answer alike pins none of them.

    Such a case would pass just as well on a box where nine of the ten engines
    had been quietly replaced by the tenth -- an assertion that holds whether or
    not the thing it names is true, which is the shape this org has spent three
    weeks pulling out of its own CI (logbook#63, logbook#64). So every case has
    to contain a disagreement.

    This replaced a per-engine version of the same idea, "no engine is only ever
    expected to agree", which read better and could not fail: every engine is
    named in `backslash-d`, `backslash-d` contains a disagreement, and so the
    predicate was satisfied for each engine by its neighbours rather than by
    anything about the engine itself. Measured, not assumed -- an eleventh
    engine added to that case with the same expectation as `grep` left it green.

    Table-only, like the test above, so it cannot skip on a box missing engines.
    """
    bland = [
        case.name
        for case in CASES
        if len({_as_key(expected) for expected in case.expect.values()}) < 2
    ]
    assert bland == [], f"these cases name engines that all answer alike: {bland}"
