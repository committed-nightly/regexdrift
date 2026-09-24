r"""Why the engines might be disagreeing.

These are the only sentences in the tool that are not an observation, and they
are kept on a short leash: **a note is printed only when a disagreement has
already been measured.** The finding is always the table of what the engines did.
A note is commentary attached to it.

That rule is the whole point. A tool that prints "`\d` is not portable" from
reading the pattern is a lint rule, and a lint rule about regex dialects would
fire constantly on patterns that are completely fine because every engine that
will ever run them agrees. This tool has run the engines. If they all agreed, it
has nothing to say and says nothing, however alarming the pattern looks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .syntax import Token, walk

CLASS_ESCAPES = set("dDwWsSh")
BARE_QUANTIFIERS = set("+?")


@dataclass(frozen=True)
class Note:
    slug: str
    text: str


def _tokens(pattern: str) -> list[Token]:
    return list(walk(pattern))


def _has_class_escape(pattern: str) -> bool:
    return any(t.kind == "escape" and t.escaped in CLASS_ESCAPES for t in _tokens(pattern))


def _has_bare_quantifier(pattern: str) -> bool:
    return any(t.kind == "char" and t.text in BARE_QUANTIFIERS for t in _tokens(pattern))


def _has_bare_brace(pattern: str) -> bool:
    return any(t.kind == "char" and t.text == "{" for t in _tokens(pattern))


def _has_bare_alternation(pattern: str) -> bool:
    return any(t.kind == "char" and t.text == "|" for t in _tokens(pattern))


def _has_bare_group(pattern: str) -> bool:
    return any(t.kind == "char" and t.text == "(" for t in _tokens(pattern))


def _has_backreference(pattern: str) -> bool:
    return any(t.kind == "escape" and t.escaped.isdigit() and t.escaped != "0" for t in _tokens(pattern))


def _has_lazy_quantifier(pattern: str) -> bool:
    tokens = _tokens(pattern)
    for earlier, later in zip(tokens, tokens[1:]):
        if later.kind == "char" and later.text == "?":
            if earlier.kind == "char" and earlier.text in "+*}":
                return True
    return False


def _has_extended_group(pattern: str) -> bool:
    return "(?" in pattern


def _has_word_boundary(pattern: str) -> bool:
    return any(t.kind == "escape" and t.escaped in "bB<>" for t in _tokens(pattern))


NOTES: list[tuple[Callable[[str], bool], Note]] = [
    (
        _has_class_escape,
        Note(
            "class-escape",
            "POSIX BRE and ERE have no \\d, \\w or \\s. In those dialects the "
            "escape is the bare letter, so \\d matches a `d`. PCRE, Python and "
            "ECMAScript read it as a character class. Nothing warns except gawk.",
        ),
    ),
    (
        _has_bare_quantifier,
        Note(
            "bre-quantifier",
            "A bare + or ? is a quantifier in ERE and an ordinary character in "
            "BRE, which spells the quantifiers \\+ and \\? as a GNU extension.",
        ),
    ),
    (
        _has_bare_brace,
        Note(
            "interval",
            "An interval is {n,m} in ERE and \\{n,m\\} in BRE, so BRE reads a "
            "bare { as a literal brace.",
        ),
    ),
    (
        _has_bare_alternation,
        Note(
            "alternation",
            "A bare | is alternation in ERE and a literal bar in BRE, which "
            "spells it \\|.",
        ),
    ),
    (
        _has_bare_group,
        Note(
            "group",
            "A bare ( groups in ERE and is a literal parenthesis in BRE, which "
            "spells grouping \\( and \\).",
        ),
    ),
    (
        _has_backreference,
        Note(
            "backreference",
            "A backreference. POSIX BRE has them, GNU supports them in ERE, and "
            "PCRE and Python do too. The RE2 family -- ripgrep, Go's regexp -- "
            "cannot, and rejects the pattern rather than matching something "
            "else, which is the failure you want.",
        ),
    ),
    (
        _has_lazy_quantifier,
        Note(
            "lazy-quantifier",
            "Lazy quantifiers (+?, *?, }?) exist in PCRE, Python and "
            "ECMAScript. POSIX has none: there, the ? is its own quantifier "
            "applied to the one before it, or a literal.",
        ),
    ),
    (
        _has_extended_group,
        Note(
            "extended-group",
            "(?...) is PCRE and ECMAScript syntax for non-capturing groups, "
            "lookaround and flags. POSIX has no such construct and reads the ? "
            "as a quantifier on the ( or as a literal.",
        ),
    ),
    (
        _has_word_boundary,
        Note(
            "word-boundary",
            "\\b, \\B, \\< and \\> are word boundaries in GNU grep and sed, and "
            "\\b is one in PCRE, Python and ECMAScript. mawk has none of them. "
            "Inside a bracket expression \\b is a backspace instead.",
        ),
    ),
]


def explain(pattern: str) -> list[Note]:
    """Notes for the constructs this pattern actually contains.

    The caller is responsible for only showing these when the engines were
    measured to disagree. See the module docstring -- that ordering is the design
    and not an implementation detail.
    """
    return [note for detect, note in NOTES if detect(pattern)]
