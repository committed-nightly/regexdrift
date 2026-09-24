r"""Walking a pattern without pretending to understand it.

Two jobs, shared by the probe generator and the notes. Both need to tell an
escape from a bare character and both need to leave bracket expressions alone,
because a `+` inside `[+]` is not a quantifier anywhere and a note or a probe
that thought otherwise would be noise.

This is deliberately not a parser. It has no notion of a valid pattern, it never
raises, and on malformed input it does something defensible and keeps going --
the engines are the ones with opinions about validity, and their opinions are the
output of this tool.
"""

from __future__ import annotations

from typing import Iterator, Literal, NamedTuple

Kind = Literal["escape", "bracket", "char"]


class Token(NamedTuple):
    """One step of a walk.

    `kind` is "escape" for a backslash pair, "bracket" for a whole bracket
    expression, "char" for anything else. `text` is the source, so joining every
    token's text reconstructs the pattern exactly.
    """

    kind: Kind
    text: str
    index: int

    @property
    def escaped(self) -> str:
        """The character after the backslash, for an escape token."""
        return self.text[1:] if self.kind == "escape" else ""


def bracket_end(pattern: str, start: int) -> int:
    """Index just past the bracket expression beginning at `start`.

    Two POSIX rules, and the second one was got wrong first time round.

    A `]` immediately after the opening bracket, or after a leading `^`, is a
    literal member rather than the close -- so `[]]` is a bracket expression
    matching one `]`, not an empty one followed by a stray bracket.

    And a `]` inside a `[: :]`, `[. .]` or `[= =]` sequence does not close the
    bracket expression either. `[[:digit:]]` is eleven characters, not ten. An
    earlier version of this claimed the first rule covered that case as well,
    which it does not, and stopped at the `]` that closes `:]` -- so
    `[[:digit:]]` came back as the bracket `[[:digit:]` plus a trailing `]`, and
    no probe for it ever contained a digit.

    An unterminated bracket comes back as the rest of the pattern. That is not a
    guess about what it means; it is a guess about where it stops, and the
    engines disagreeing about whether it is valid at all is a finding this tool
    is happy to report.
    """
    i = start + 1
    if i < len(pattern) and pattern[i] == "^":
        i += 1
    if i < len(pattern) and pattern[i] == "]":
        i += 1
    while i < len(pattern):
        if pattern[i] == "\\" and i + 1 < len(pattern):
            i += 2
            continue
        if pattern[i] == "[" and i + 1 < len(pattern) and pattern[i + 1] in ":.=":
            closing = pattern[i + 1] + "]"
            end = pattern.find(closing, i + 2)
            if end != -1:
                i = end + 2
                continue
        if pattern[i] == "]":
            return i + 1
        i += 1
    return len(pattern)


def walk(pattern: str) -> Iterator[Token]:
    """Tokens of a pattern, left to right.

    A trailing lone backslash is a "char" token, because that is all anyone can
    honestly say about it.
    """
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            yield Token("escape", pattern[i : i + 2], i)
            i += 2
            continue
        if char == "[":
            end = bracket_end(pattern, i)
            yield Token("bracket", pattern[i:end], i)
            i = end
            continue
        yield Token("char", char, i)
        i += 1
