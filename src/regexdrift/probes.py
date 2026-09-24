r"""Making up input that might tell the engines apart.

Run with a file, this module is not used at all. It exists for the case where
you have a pattern and no corpus, and the question is still worth answering.

**The probes are guesses. The results are never guesses.** Nothing in here decides
anything -- it writes candidate lines, every engine is then run against all of
them for real, and the report only ever describes what the engines did. A wrong
guess in this file costs a useless probe. It cannot produce a wrong finding,
which is the only reason a heuristic is allowed anywhere near this tool.

The guessing method is to read the pattern several ways on purpose, because that
is precisely what the engines do to each other. Two axes:

*Escapes.* `\d` is a digit class in PCRE, Python and ECMAScript, and in POSIX
BRE and ERE it is the letter `d` -- so one reading substitutes a digit and
another substitutes a `d`.

*Quantifiers.* `+` repeats in ERE and is a plain `+` in BRE, so one reading
repeats the preceding unit and another leaves a literal `+` in the string. `*`
and `?` additionally get a reading where they contribute nothing, since matching
zero of something is the case a maximal reading would miss.

Alternation is split before any of that, and the pattern itself is always probe
number one, which is what catches a pattern that some engine reads as nothing but
literal text.

What this does not do is parse a regular expression. It walks the pattern once,
groups it loosely, and is happy to be wrong.
"""

from __future__ import annotations

from .syntax import bracket_end

# How many copies of a unit a `+` or `*` reading emits. Three, so that a probe
# distinguishes "one or more" from "exactly one" as well as from zero.
REPS = 3

MAX_PROBES = 32

# One sample character per escape that means a character class somewhere. Only
# single-line samples: a probe containing a newline is not a line, and every
# engine here works a line at a time.
CLASS_SAMPLES = {
    "d": "7",
    "D": "x",
    "w": "a",
    "W": "!",
    "s": " ",
    "S": "x",
    "h": " ",
    "H": "x",
    "t": "\t",
}

QUANTIFIER_CHARS = "+*?"

# Substituted where the pattern says "any character".
ANY_SAMPLE = "x"

# Substituted for a negated bracket expression, and used when a bracket
# expression turns out to have nothing usable in it.
OUTSIDE_SAMPLE = "x"


def _split_alternation(pattern: str) -> list[str]:
    """Top-level branches of the pattern, on `|` and on BRE's `\\|`.

    Depth is tracked through both spellings of a group, and bracket expressions
    are skipped whole, because a `|` inside either is not a branch.
    """
    branches: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            if nxt == "(":
                depth += 1
            elif nxt == ")":
                depth = max(0, depth - 1)
            elif nxt == "|" and depth == 0:
                branches.append("".join(current))
                current = []
                i += 2
                continue
            current.append(pattern[i : i + 2])
            i += 2
            continue
        if char == "[":
            end = bracket_end(pattern, i)
            current.append(pattern[i:end])
            i = end
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "|" and depth == 0:
            branches.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    branches.append("".join(current))
    return branches


def _bracket_member(expression: str) -> str:
    """A character the bracket expression plausibly accepts."""
    inner = expression[1:-1] if expression.endswith("]") else expression[1:]
    if inner.startswith("^"):
        return OUTSIDE_SAMPLE
    if inner.startswith("[:") and ":]" in inner:
        name = inner[2 : inner.index(":]")]
        return {
            "digit": "7",
            "alpha": "a",
            "alnum": "a",
            "upper": "A",
            "lower": "a",
            "space": " ",
            "punct": "!",
            "xdigit": "f",
            "blank": " ",
        }.get(name, "a")
    for i, char in enumerate(inner):
        if char == "\\":
            continue
        if char == "-" and 0 < i < len(inner) - 1:
            continue
        return char
    return OUTSIDE_SAMPLE


def _repeat(units: list[str], count: int) -> None:
    """Apply a quantifier to the last emitted unit, in place."""
    if not units:
        return
    last = units.pop()
    if count > 0:
        units.append(last * count)


def _read(branch: str, *, classes: bool, quantifiers: str) -> str:
    """One reading of one branch.

    `classes` -- whether `\\d` and friends stand for a class member or for the
    bare letter. `quantifiers` -- "literal", "max" or "min".
    """
    units: list[str] = []
    group_starts: list[tuple[int, int]] = []
    captures: dict[int, str] = {}
    opened = 0
    i = 0

    def close_group() -> None:
        """Collapse a finished group into one unit and remember what it held.

        Remembering it is what lets `\\1` become `a` rather than `1`, so that
        `(a)\\1` gets a probe of `aa` -- the string a backreference actually
        matches. Without it the only probes are the ones that read the `\\1` as a
        digit, every engine matches none of them, and the report truthfully says
        nobody disagreed while missing the entire point of the pattern.
        """
        nonlocal units
        if not group_starts:
            return
        start, number = group_starts.pop()
        collapsed = "".join(units[start:])
        del units[start:]
        units.append(collapsed)
        captures[number] = collapsed

    while i < len(branch):
        char = branch[i]

        if char == "\\" and i + 1 < len(branch):
            nxt = branch[i + 1]
            if nxt in "()":
                # BRE spells grouping with backslashes.
                if quantifiers == "literal":
                    units.append("\\" + nxt)
                elif nxt == "(":
                    opened += 1
                    group_starts.append((len(units), opened))
                else:
                    close_group()
                i += 2
                continue
            if classes and nxt.isdigit() and nxt != "0" and int(nxt) in captures:
                units.append(captures[int(nxt)])
                i += 2
                continue
            if nxt in QUANTIFIER_CHARS or nxt == "{":
                # `\+` is a GNU BRE quantifier and a literal `+` under POSIX.
                if quantifiers == "literal":
                    units.append(nxt)
                elif nxt == "?":
                    _repeat(units, 1 if quantifiers == "max" else 0)
                elif nxt == "{":
                    end = branch.find("\\}", i)
                    _repeat(units, _interval_count(branch[i + 2 : end if end != -1 else len(branch)]))
                    i = (end + 2) if end != -1 else len(branch)
                    continue
                else:
                    _repeat(units, REPS if quantifiers == "max" else (1 if nxt == "+" else 0))
                i += 2
                continue
            if classes and nxt in CLASS_SAMPLES:
                units.append(CLASS_SAMPLES[nxt])
            else:
                units.append(nxt)
            i += 2
            continue

        if char == "[":
            end = bracket_end(branch, i)
            expression = branch[i:end]
            units.append(_bracket_member(expression) if classes else expression)
            i = end
            continue

        if char in QUANTIFIER_CHARS:
            if quantifiers == "literal":
                units.append(char)
            elif char == "?":
                _repeat(units, 1 if quantifiers == "max" else 0)
            else:
                _repeat(units, REPS if quantifiers == "max" else (1 if char == "+" else 0))
            i += 1
            continue

        if char == "{":
            end = branch.find("}", i)
            if quantifiers == "literal" or end == -1:
                units.append(char)
                i += 1
                continue
            _repeat(units, _interval_count(branch[i + 1 : end]))
            i = end + 1
            continue

        if char == "(":
            if quantifiers == "literal":
                units.append(char)
            else:
                opened += 1
                group_starts.append((len(units), opened))
            i += 1
            continue

        if char == ")":
            if quantifiers == "literal":
                units.append(char)
            else:
                close_group()
            i += 1
            continue

        if char in "^$":
            if quantifiers == "literal":
                units.append(char)
            i += 1
            continue

        if char == ".":
            units.append(ANY_SAMPLE if classes else char)
            i += 1
            continue

        units.append(char)
        i += 1

    return "".join(units)


def _interval_count(body: str) -> int:
    """How many copies `{...}` asks for, as far as a probe needs to care."""
    head = body.split(",")[0].strip()
    if head.isdigit():
        return int(head)
    return REPS


def generate(pattern: str) -> list[str]:
    """Candidate input lines for a pattern, most literal first.

    The pattern itself leads, then each branch verbatim, then the readings. Order
    is deterministic so the report is stable between runs.
    """
    candidates: list[str] = [pattern]
    branches = _split_alternation(pattern)
    if len(branches) > 1:
        candidates.extend(branches)
    for branch in branches:
        for classes in (False, True):
            for quantifiers in ("literal", "max", "min"):
                candidates.append(_read(branch, classes=classes, quantifiers=quantifiers))

    seen: set[str] = set()
    probes: list[str] = []
    for candidate in candidates:
        # A probe has to be able to *be* a line. Nothing here works on input
        # containing a newline or a NUL, so such a candidate is dropped rather
        # than silently truncated into a different probe than the one intended.
        if "\n" in candidate or "\0" in candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        probes.append(candidate)
        if len(probes) >= MAX_PROBES:
            break
    return probes
