r"""Turning ten answers into one finding.

Engines that accepted the pattern are grouped by the set of line numbers they
matched. One group means they agree. More than one means the same pattern means
different things to different engines and nothing told you.

Two distinctions are load-bearing.

**A refusal is not a disagreement.** ripgrep rejecting a backreference is a
portability problem you cannot fail to notice: the command exits 2 and prints a
parse error. The disagreement this tool is named after is the quiet one, where
every engine exits 0 and two of them hand back different lines. So refusals are
reported in full and, by default, do not set the exit code. `--strict` is for
people who want a green tick to mean every engine ran it.

**An engine nobody could run is not an engine that agreed.** Missing binaries are
listed by name. The alternative -- quietly comparing the four engines that happen
to be installed and reporting agreement -- is how a check ends up passing for a
year without testing anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engines import Engine, Result, Status


@dataclass(frozen=True)
class Group:
    """Engines that matched exactly the same lines."""

    matched: frozenset[int]
    engines: tuple[str, ...]


@dataclass(frozen=True)
class Comparison:
    pattern: str
    lines: tuple[str, ...]
    results: tuple[Result, ...]
    groups: tuple[Group, ...]

    @property
    def accepted(self) -> tuple[Result, ...]:
        return tuple(r for r in self.results if r.status is Status.ACCEPTED)

    @property
    def rejected(self) -> tuple[Result, ...]:
        return tuple(r for r in self.results if r.status is Status.REJECTED)

    @property
    def unavailable(self) -> tuple[Result, ...]:
        return tuple(r for r in self.results if r.status is Status.UNAVAILABLE)

    @property
    def warned(self) -> tuple[Result, ...]:
        return tuple(r for r in self.accepted if r.warning)

    @property
    def drifted(self) -> bool:
        """Two or more engines accepted the pattern and disagreed."""
        return len(self.groups) > 1

    @property
    def distinguishing(self) -> tuple[int, ...]:
        """Line numbers the accepting engines do not all agree about.

        These are the rows worth printing. On a large input almost every line is
        agreed about and showing them all would bury the handful that are not.
        """
        if len(self.groups) < 2:
            return ()
        everywhere = set.intersection(*(set(g.matched) for g in self.groups))
        anywhere = set.union(*(set(g.matched) for g in self.groups))
        return tuple(sorted(anywhere - everywhere))

    def line(self, number: int) -> str:
        """The text of a 1-based line number."""
        return self.lines[number - 1]


def group_results(results: list[Result]) -> tuple[Group, ...]:
    """Accepting engines, grouped by what they matched.

    Groups come out ordered by the engine order they were run in, so the report
    is stable and the first group is the first engine's behaviour rather than
    whichever set happened to hash first.
    """
    order: list[frozenset[int]] = []
    members: dict[frozenset[int], list[str]] = {}
    for result in results:
        if result.status is not Status.ACCEPTED:
            continue
        if result.matched not in members:
            members[result.matched] = []
            order.append(result.matched)
        members[result.matched].append(result.engine)
    return tuple(Group(matched, tuple(members[matched])) for matched in order)


def compare(pattern: str, lines: list[str], results: list[Result]) -> Comparison:
    return Comparison(
        pattern=pattern,
        lines=tuple(lines),
        results=tuple(results),
        groups=group_results(results),
    )


def engine_dialects(engines: list[Engine]) -> dict[str, str]:
    return {engine.name: engine.dialect for engine in engines}
