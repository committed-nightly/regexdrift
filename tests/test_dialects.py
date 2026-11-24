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

from regexdrift.engines import BY_NAME, ENGINES, Status

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
    reason the report prints, which is `Engine.complaint`: the engine's own
    rule for where, in what it wrote, the reason actually is.

    The reason and not the whole of stderr, because "bad escape" appears in an
    uncaught Python traceback as well as in a legible refusal, so a substring
    match over the full message passed both before and after the fix that made
    python's refusal readable. A whole thing matched by a substring is how an
    assertion stops asserting its own sentence (logbook#63), and that is no
    better in a test than it is in a CI script.

    Every `expect` string is the construct the engine named, ripgrep's included.
    There was a second field here, `detail`, for a substring that had to appear
    anywhere in the full complaint. It existed because ripgrep's visible line
    was the generic "regex parse error:" while the construct it refused sat four
    lines down, so the thing worth pinning was not the thing the report showed.
    The report shows it now (logbook#80), and a workaround kept past its bug is
    one more thing that has to stay true, so it is gone.

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
            "rg": "unrecognized escape sequence",
        },
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
            "rg": "backreferences are not supported",
        },
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
        expect={
            "grep -P": {1},
            "python": {1},
            "node": {1},
            "rg": "look-around, including look-ahead and look-behind, is not supported",
        },
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
            visible = BY_NAME[name].complaint(result.message)
            assert expected in visible, (
                f"{name} refused {case.pattern!r}, and the reason the report "
                f"prints is {visible!r}, not the pinned {expected!r}"
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


@dataclass(frozen=True)
class Refusal:
    """One engine, a pattern it will not compile, and the reason it gives.

    The table above is about what the engines *match*. This one is about what
    they say when they will not answer at all, which went unpinned for two
    months for the same reason logbook#74's gaps did: nothing counted the
    engines against the cases. Four of the ten were reporting their own
    bookkeeping in place of a reason, and one -- node -- was reporting the
    location of the throw and nothing else (logbook#80).

    `says` is a substring of the reason the report prints. `absent` is the noise
    that used to be printed instead, asserted gone: these are the strings that
    made the difference between a report naming a construct and a report naming
    a file, a character offset, or `[eval]:1`.
    """

    engine: str
    pattern: str
    says: str
    absent: tuple[str, ...] = ()
    why: str = ""


REFUSALS = [
    Refusal("grep", r"(a)\1", "Invalid back reference",
            why="BRE has backreferences; \\1 with one group is still unmatched"),
    Refusal("grep -E", "(a", "Unmatched ( or \\(",
            why="the control: GNU grep's first line is the reason and always was"),
    Refusal("grep -P", r"\ycat\y", "unrecognized character follows \\",
            why="\\y is gawk's word boundary and PCRE2 has no such escape"),
    Refusal("sed", r"(a)\1", "Invalid back reference",
            absent=("expression #1", "char "),
            why="sed counts characters into the \\%PAT%= this tool wraps the "
                "pattern in, so its offset is a precise wrong answer about the "
                "pattern the caller wrote"),
    Refusal("sed -E", "(a", "Unmatched ( or \\(",
            absent=("expression #1", "char "),
            why="the second sed, because the two share an extractor and only "
                "one of them was ever exercised by a refusal"),
    Refusal("gawk", "(a", "Unmatched ( or \\(",
            absent=("FNR=", "FILENAME", "cmd. line"),
            why="gawk names the input file and the record number before the "
                "reason, and the file is the CLI's scratch path"),
    Refusal("mawk", "(a", "missing ')'",
            absent=("FILENAME",),
            why="mawk needs no extractor -- its first line is the reason and "
                "the FILENAME is on its third, dropped. Here so that stays true"),
    Refusal("python", "(a", "unterminated subpattern",
            absent=("Traceback",),
            why="the guarded compile from #3, pinned in this table too so an "
                "unguarded one goes red here as well as in test_engines"),
    Refusal("node", "(a", "Unterminated group",
            absent=("[eval]", "require('fs')", "at new RegExp"),
            why="the same fix as python's, two months later: node's uncaught "
                "throw reported the location and pasted this program back"),
    Refusal("rg", r"(a)\1", "backreferences are not supported",
            absent=("regex parse error",),
            why="the README's own example of the loud kind of refusal, which "
                "the report rendered as the generic header four lines above it"),
]


@pytest.mark.parametrize(
    "case", [pytest.param(c, id=c.engine.replace(" ", "-"), marks=requires(c.engine)) for c in REFUSALS]
)
def test_each_engine_says_which_construct_it_refused(case, write_lines):
    """Run the engine for real and read the line the report would print."""
    path = write_lines(["aa", "ab", "cat"])
    engine = BY_NAME[case.engine]
    result = engine.run(case.pattern, path)

    assert result.status is Status.REJECTED, (
        f"{case.engine} was expected to refuse {case.pattern!r}, got "
        f"{result.status.value}. {case.why}"
    )
    reason = engine.complaint(result.message)
    assert case.says in reason, (
        f"{case.engine} refused {case.pattern!r} and the report would print "
        f"{reason!r}, which does not name {case.says!r}. {case.why}"
    )
    for noise in case.absent:
        assert noise not in reason, (
            f"{case.engine}'s reason still carries {noise!r}: {reason!r}. {case.why}"
        )
    # Universal, and the cheapest statement of the whole bug: a reason is one
    # line. node's was sixteen and ripgrep's seven, and the report prints one.
    assert len(reason.splitlines()) == 1, f"{case.engine}'s reason is not one line: {reason!r}"
    # The raw message is kept whole for --json, so the fix is a reading of what
    # the engine said and not a truncation of it.
    assert reason in result.message or reason in result.message.replace("\n", " "), (
        f"{case.engine}'s reason is not a substring of what it actually wrote: "
        f"{reason!r} vs {result.message!r}"
    )


def test_every_engine_has_a_refusal_case():
    """Add an engine and this goes red until something pins how it says no.

    The counterpart of test_every_engine_has_a_dialect_case, and the guard that
    would have caught logbook#80 two months earlier: node and both seds had no
    refusal pinned anywhere, so `[eval]:1` was an answer nothing was reading.
    Table-only, so a box missing half the engines cannot skip its way past it.
    """
    named = {case.engine for case in REFUSALS}
    assert sorted({e.name for e in ENGINES} - named) == []
