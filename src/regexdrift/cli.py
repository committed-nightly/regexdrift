r"""Command line entry point.

Exit codes, because the reason to run this in CI is to fail a build:

    0  every engine that accepted the pattern matched the same lines
    1  they did not
    2  the question could not be answered

2 covers fewer than two engines accepting the pattern -- no engines installed, or
a pattern only one dialect will compile. That is not agreement. One engine cannot
agree with anything, and a tool that reported "no drift" because it found one grep
would be lying in the most comfortable possible direction.

`--strict` additionally fails on a refusal or a warning, for anyone who wants a
green tick to mean all ten engines ran the pattern without comment.

One thing the output is careful about: in probe mode a 0 is reported as "agreed on
these N probes" and never as "the engines agree". The probes are generated guesses
(see probes.py) and absence of a difference across them is not evidence of
absence. With a real corpus the claim is about that corpus. Either way the tool
only ever claims what it measured.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import probes as probe_module
from .compare import Comparison, compare
from .engines import ENGINES, Engine, Result, installed, select_engines
from .notes import explain

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

MAX_SHOWN_LINES = 8
MAX_LINE_WIDTH = 60


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regexdrift",
        description=(
            "Run one pattern through every regex engine on the box and report "
            "where they silently disagree."
        ),
        epilog=(
            "Exit 0 if every engine that accepted the pattern matched the same "
            "lines, 1 if they did not, 2 if fewer than two engines could answer."
        ),
    )
    parser.add_argument("pattern", nargs="?", help="the pattern, passed to every engine unchanged")
    parser.add_argument(
        "path",
        nargs="?",
        help="input to match against; - for stdin. Omitted, probes are generated.",
    )
    parser.add_argument(
        "--engine",
        action="append",
        dest="engines",
        metavar="NAME",
        choices=[e.name for e in ENGINES],
        help="restrict to this engine; repeatable. --list-engines for the names.",
    )
    parser.add_argument(
        "--list-engines",
        action="store_true",
        help="print the engines, their dialects, and whether they are installed",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also exit 1 if any engine refused the pattern or warned about it",
    )
    parser.add_argument(
        "--all-lines",
        action="store_true",
        help="show every matched line, not only the ones the engines disagree about",
    )
    return parser


def read_lines(path: str | None, pattern: str) -> tuple[list[str], bool]:
    """The input lines, and whether they were generated.

    Stdin is read only when asked for with `-`. The first version of this guessed
    -- no path and `stdin.isatty()` false meant read stdin, so that
    `cat log | regexdrift PAT` would work without a flag. It hangs. An fd that is
    not a terminal is not necessarily an fd that anybody is going to write to or
    close, which is the normal state of stdin under a CI runner or any process
    supervisor, and the tool sat there for two minutes with nothing to say.

    So no guessing. No path means probes, and `render` prints which it used on the
    second line of every report -- the risk being swapped for the hang is that
    somebody pipes a file in and gets probes, and that is a visible mistake
    rather than a silent one.
    """
    if path == "-":
        return sys.stdin.read().splitlines(), False
    if path is not None:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines(), False
    return probe_module.generate(pattern), True


def _show(text: str) -> str:
    """A line as it appears in the report: visible, bounded, unambiguous."""
    shown = text.replace("\t", "\\t")
    if len(shown) > MAX_LINE_WIDTH:
        shown = shown[: MAX_LINE_WIDTH - 1] + "…"
    return f"`{shown}`" if shown else "(empty line)"


def _tidy(result: Result, temp_path: str) -> Result:
    """Replace the scratch file's name in anything an engine said about it.

    gawk names the input in its warnings, so the report would otherwise carry
    `/tmp/regexdrift-cnfxscn9` into whatever reads it -- different on every run,
    which makes output that cannot be diffed between two runs of the same
    pattern, and a test that cannot assert on the message.
    """
    if not (result.message or result.warning):
        return result
    return Result(
        engine=result.engine,
        status=result.status,
        matched=result.matched,
        message=result.message.replace(temp_path, "<input>"),
        warning=result.warning.replace(temp_path, "<input>"),
    )


def _strip_prefix(engine: str, text: str) -> str:
    """The first line of an engine's complaint, without it re-introducing itself."""
    first = text.splitlines()[0] if text else ""
    binary = engine.split()[0]
    if first.startswith(f"{binary}: "):
        first = first[len(binary) + 2 :]
    return first


def _list_engines() -> int:
    width = max(len(e.name) for e in ENGINES)
    for engine in ENGINES:
        state = "installed" if engine.available() else f"missing ({engine.binary})"
        print(f"  {engine.name:<{width}}  {engine.dialect:<32}  {state}")
    return EXIT_OK


def render(comparison: Comparison, chosen: list[Engine], generated: bool, all_lines: bool) -> None:
    dialects = {engine.name: engine.dialect for engine in chosen}
    print(f"pattern  {comparison.pattern}")
    source = (
        f"{len(comparison.lines)} generated probes — pass a FILE or - to use your own"
        if generated
        else f"{len(comparison.lines)} lines"
    )
    print(f"input    {source}")
    print()

    accepted = len(comparison.accepted)
    if comparison.drifted:
        print(f"{len(comparison.groups)} behaviours among the {accepted} engines that accepted it.")
    elif accepted:
        where = f"these {len(comparison.lines)} probes" if generated else "this input"
        print(f"All {accepted} engines that accepted it matched the same lines on {where}.")
    print()

    interesting = comparison.distinguishing
    for number, group in enumerate(comparison.groups, 1):
        names = " · ".join(group.engines)
        print(f"  group {number}   {names}")
        print(f"            {', '.join(dialects.get(n, '?') for n in group.engines)}")
        wanted = [n for n in sorted(group.matched) if all_lines or not interesting or n in interesting]
        if not group.matched:
            print("            matched nothing")
        elif not wanted:
            print(f"            matched {len(group.matched)} lines, none of them in dispute")
        else:
            shown = wanted[:MAX_SHOWN_LINES]
            rendered = "  ".join(
                (_show(comparison.line(n)) if generated else f"{n}:{_show(comparison.line(n))}")
                for n in shown
            )
            extra = f"  (+{len(wanted) - len(shown)} more)" if len(wanted) > len(shown) else ""
            label = "matched" if all_lines or not interesting else "matched, of the disputed lines:"
            print(f"            {label} {rendered}{extra}")
        print()

    for result in comparison.rejected:
        why = _strip_prefix(result.engine, result.message) or "no reason given"
        print(f"  refused   {result.engine}: {why}")
    for result in comparison.warned:
        print(f"  warned    {result.engine}: {_strip_prefix(result.engine, result.warning)}")
    if comparison.unavailable:
        print(f"  not run   {', '.join(r.engine for r in comparison.unavailable)} (not installed)")
    if comparison.rejected or comparison.warned or comparison.unavailable:
        print()

    if comparison.drifted:
        notes = explain(comparison.pattern)
        if notes:
            print("why they might differ")
            for note in notes:
                print(f"  · {note.text}")
            print()


def to_json(comparison: Comparison, chosen: list[Engine], generated: bool) -> dict:
    dialects = {engine.name: engine.dialect for engine in chosen}
    return {
        "pattern": comparison.pattern,
        "input": {"generated": generated, "lines": list(comparison.lines)},
        "drift": comparison.drifted,
        "disputed_lines": list(comparison.distinguishing),
        "groups": [
            {
                "engines": list(group.engines),
                "dialects": [dialects.get(n, "") for n in group.engines],
                "matched": sorted(group.matched),
            }
            for group in comparison.groups
        ],
        "refused": [{"engine": r.engine, "message": r.message} for r in comparison.rejected],
        "warned": [{"engine": r.engine, "message": r.warning} for r in comparison.warned],
        "not_installed": [r.engine for r in comparison.unavailable],
        "notes": [{"slug": n.slug, "text": n.text} for n in explain(comparison.pattern)]
        if comparison.drifted
        else [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_engines:
        return _list_engines()
    if args.pattern is None:
        parser.error("a pattern is required (or --list-engines)")

    chosen = select_engines(args.engines)
    if not installed(chosen):
        wanted = ", ".join(args.engines) if args.engines else "any engine"
        print(f"regexdrift: none of {wanted} is installed, so nothing was compared", file=sys.stderr)
        return EXIT_ERROR

    try:
        lines, generated = read_lines(args.path, args.pattern)
    except OSError as exc:
        print(f"regexdrift: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not lines:
        print("regexdrift: no input lines, so nothing was compared", file=sys.stderr)
        return EXIT_ERROR

    # Every engine reads the same file rather than each getting its own copy, so
    # a difference in the report cannot come from a difference in the input.
    handle, path = tempfile.mkstemp(prefix="regexdrift-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write("".join(line + "\n" for line in lines))
        results = [_tidy(engine.run(args.pattern, path), path) for engine in chosen]
    finally:
        os.unlink(path)

    comparison = compare(args.pattern, lines, results)

    if args.json:
        print(json.dumps(to_json(comparison, chosen, generated), indent=2))
    else:
        render(comparison, chosen, generated, args.all_lines)

    if len(comparison.accepted) < 2:
        print(
            f"regexdrift: only {len(comparison.accepted)} engine accepted this pattern, "
            "which is not enough to compare",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if comparison.drifted:
        return EXIT_DRIFT
    if args.strict and (comparison.rejected or comparison.warned):
        return EXIT_DRIFT
    return EXIT_OK


def run() -> None:  # console_scripts entry point
    sys.exit(main())


if __name__ == "__main__":
    run()
