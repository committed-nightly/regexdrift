r"""The engines, and how to ask each one the same question.

Every answer this tool gives comes from running a real engine. Nothing here
implements or parses a regular expression, and that is the whole design. The
disagreements between these dialects are the subject of the tool, so a
hand-written model of them would be a second opinion competing with the thing it
is describing -- and the first time the model was wrong, the tool would be
confidently wrong about the exact question it exists to answer.

So: one question, asked ten ways. "Which line numbers of this file match this
pattern?" Line *numbers* rather than lines, because two identical lines in the
input would otherwise be indistinguishable in the output and the answers could
not be compared.

Three things had to be got right to ask that question honestly.

**The pattern must reach the engine as bytes, not as source code.** Every adapter
passes it through argv or the environment, never by pasting it into a program
text or a shell line. `grep -e PAT` rather than `grep PAT`, so a pattern that
starts with `-` is a pattern. awk takes it from `ENVIRON` and not from `-v`,
because `-v` runs escape processing over the value first:

    $ gawk -v p='\d+' '$0 ~ p' ...
    gawk: warning: escape sequence `\d' treated as plain `d'

That warning is gawk mangling the pattern before its regex engine ever sees it,
which would make the tool's report a report about gawk's assignment rules.

**sed has no way to pass a pattern out of band at all**, so the pattern goes
inside `\cPATc` and the delimiter `c` is chosen from characters absent from the
pattern *and* meaningless to a regex. If every candidate appears in the pattern,
sed is reported unavailable for that pattern rather than guessed at.

**An engine refusing a pattern is not the same as an engine matching nothing.**
grep and ripgrep separate those by exit status, 1 against 2. sed does not -- it
exits 0 whether or not a line matched -- so for sed the presence of output is the
match and a non-zero exit is the refusal. Conflating the two would turn the
loudest, most useful outcome in this tool (ripgrep rejecting a backreference
outright) into a silent empty result.

And a fourth, which the first version of this file got wrong. **An engine
refusing a pattern is not the same as an engine that cannot run.** Three of the
ten entries below -- `grep -E`, `grep -P`, `sed -E` -- are modes of a binary
rather than binaries, and `shutil.which` cannot see a mode. PCRE support is a
compile-time option in GNU grep and absent from BSD and busybox grep entirely,
so on those boxes `grep -P` is on PATH, is not there, and said so by exiting 2:

    $ busybox grep -P -e 'a' /dev/null
    grep: invalid option -- 'P'

Exit 2 is this file's definition of a refusal, so PCRE2 -- missing -- was
reported as PCRE2 having an opinion about your pattern. The fix is the same
move as everything else here: ask the engine. On any refusal the adapter asks
again with `PROBE_PATTERN`, which every dialect in the list compiles. An engine
that will not take that one will not take any, so it is UNAVAILABLE and named
as such; an engine that takes it has genuinely refused what you asked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum

TIMEOUT_SECONDS = 10

# A pattern every dialect in ENGINES compiles: one literal character, no
# quantifier, no class, no escape. BRE, ERE, PCRE2, awk's ERE, Python `re`,
# ECMAScript and Rust's regex all take it, so an engine that refuses it is not
# disagreeing about syntax -- it cannot answer at all.
PROBE_PATTERN = "a"

# The probe is asked against os.devnull rather than the caller's input: it is
# always readable, needs no temp file, and costs no read. Matching nothing is
# still a compiled pattern, which is the only thing being established here --
# grep and rg exit 1, sed and awk exit 0, and all four mean "accepted".
PROBE_PATH = os.devnull

# Characters tried as a sed address delimiter, in order. None of them mean
# anything to a POSIX regex, so wrapping a pattern in one cannot change how the
# pattern is read -- and one absent from the pattern needs no escaping inside it.
SED_DELIMITERS = "%#,~@:;!_=<>|+&^"


class Status(Enum):
    """What came back from an engine.

    ACCEPTED covers "compiled the pattern and matched nothing", which is an
    answer. REJECTED is the engine saying no to the pattern you asked about.
    UNAVAILABLE is no answer being obtainable, for any of three reasons: the
    binary is not on PATH, the pattern leaves sed no delimiter, or the binary is
    there and this mode of it is not (see the module docstring). It is never
    folded into either of the others -- a tool that reports agreement because
    half the engines were missing is the failure this whole shop exists to find,
    and one that files a missing engine as a refusal is the same failure wearing
    a hat.
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


class Readiness(Enum):
    """Whether an engine can be asked anything at all, which is three states.

    `--list-engines` reported two of these for its first two months, by calling
    `shutil.which` and printing the answer as "installed". MISSING and UNUSABLE
    are both "you will get no answer from this engine", but they need different
    things doing about them -- one is a package to install, the other is a grep
    that will never have PCRE no matter what you install -- so they are not one
    state with two messages.
    """

    READY = "ready"
    MISSING = "missing"
    UNUSABLE = "unusable"


def first_complaint(binary: str, text: str) -> str:
    """The first line of what an engine said, without it re-introducing itself.

    Every engine here prefixes its errors with its own name, and the report has
    already named the engine in the column to the left, so `grep -P: grep:
    invalid option` reads as a stutter.
    """
    first = text.splitlines()[0] if text else ""
    prefix = f"{binary}: "
    return first[len(prefix):] if first.startswith(prefix) else first


@dataclass(frozen=True)
class Result:
    """One engine's answer about one pattern and one set of lines."""

    engine: str
    status: Status
    matched: frozenset[int] = frozenset()
    message: str = ""
    warning: str = ""
    # A timeout is a real property of this pattern on this engine, so it stays a
    # refusal and is never re-asked as a readiness question. Without the flag the
    # probe would have to recognise the timeout by its message text.
    timed_out: bool = False

    @property
    def accepted(self) -> bool:
        return self.status is Status.ACCEPTED


@dataclass(frozen=True)
class Engine:
    """An engine, its dialect, and how to run it.

    `binary` is what has to be on PATH. `argv` builds the command for a pattern
    and a path. `reads_numbers` adapters print one line number per match; the
    only variation is how a refusal is told apart from no matches, which is what
    `interpret` decides.
    """

    name: str
    dialect: str
    binary: str
    _argv: object = field(repr=False)
    _env: object = field(default=None, repr=False)
    # grep-style: 0 matched, 1 matched nothing, >=2 could not. sed-style: any
    # non-zero is a refusal and no output means no matches.
    grep_exit_convention: bool = True

    def available(self) -> bool:
        """Whether the binary is on PATH.

        Not whether this engine can answer -- `grep -P` and `grep` share a
        binary and do not share PCRE support. Use `readiness` for that question;
        this is only the cheap half of it.
        """
        return shutil.which(self.binary) is not None

    def readiness(self) -> tuple[Readiness, str]:
        """Whether this engine can answer, and if not, why not.

        Costs one subprocess per engine when it gets past PATH, which is what it
        takes to know: the only authority on whether this grep has PCRE is this
        grep.
        """
        if not self.available():
            return Readiness.MISSING, f"{self.binary} is not on PATH"
        probe = self._attempt(PROBE_PATTERN, PROBE_PATH)
        if probe.status is Status.REJECTED:
            return Readiness.UNUSABLE, self._unusable(probe)
        if probe.status is Status.UNAVAILABLE:
            # which() said yes and the exec said no: a binary deleted between the
            # two calls. Rare, and reporting it as ready would be a lie.
            return Readiness.MISSING, probe.message
        return Readiness.READY, ""

    def _unusable(self, probe: Result) -> str:
        """Why a refusal of PROBE_PATTERN is a verdict on the engine.

        Quotes the pattern, because the claim is only as good as the pattern
        being uncontroversial and the reader should be able to check it.
        """
        why = first_complaint(self.binary, probe.message) or "no reason given"
        return f"{self.binary} is on PATH but refused {PROBE_PATTERN!r}, which every dialect takes: {why}"

    def run(self, pattern: str, path: str) -> Result:
        """This engine's answer, or the reason there is not one.

        A refusal is re-asked once, with `PROBE_PATTERN`. That turns the one
        state this adapter could not previously name -- the mode its binary does
        not have -- from a refusal into UNAVAILABLE, at the cost of a second
        subprocess on a path that was already failing.
        """
        if not self.available():
            return Result(self.name, Status.UNAVAILABLE, message=f"{self.binary} is not on PATH")

        result = self._attempt(pattern, path)
        if result.status is Status.REJECTED and not result.timed_out:
            probe = self._attempt(PROBE_PATTERN, PROBE_PATH)
            if probe.status is Status.REJECTED:
                return Result(self.name, Status.UNAVAILABLE, message=self._unusable(probe))
        return result

    def _attempt(self, pattern: str, path: str) -> Result:
        """Run the engine once and read its exit status. No interpretation."""
        argv = self._argv(pattern, path)  # type: ignore[operator]
        if argv is None:
            return Result(
                self.name,
                Status.UNAVAILABLE,
                message="no delimiter available for this pattern",
            )

        env = dict(os.environ)
        if self._env is not None:
            env.update(self._env(pattern))  # type: ignore[operator]

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                env=env,
                timeout=TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return Result(self.name, Status.UNAVAILABLE, message=f"{self.binary} vanished from PATH")
        except subprocess.TimeoutExpired:
            # A pattern that backtracks forever is a real property of the engine
            # and worth saying out loud, but it is not an answer.
            return Result(
                self.name,
                Status.REJECTED,
                message=f"did not finish within {TIMEOUT_SECONDS}s",
                timed_out=True,
            )

        stderr = proc.stderr.strip()

        if self.grep_exit_convention:
            if proc.returncode >= 2:
                return Result(self.name, Status.REJECTED, message=stderr or f"exit {proc.returncode}")
        elif proc.returncode != 0:
            return Result(self.name, Status.REJECTED, message=stderr or f"exit {proc.returncode}")

        return Result(self.name, Status.ACCEPTED, parse_numbers(proc.stdout), warning=stderr)


def parse_numbers(stdout: str) -> frozenset[int]:
    """The line numbers in an engine's output.

    Two shapes arrive here. grep and ripgrep prefix the matching line with
    `N:`, and awk, sed, python and node are asked to print `N` alone. Splitting
    on whitespace and keeping the digits handles neither: `3:d+` is one token and
    is not a number, which cost an evening of grep appearing to match nothing at
    all while sed matched fine.

    So: one record per output line, and the number is whatever precedes the first
    colon. A bare `3` has no colon and is taken whole. A matched line whose own
    text starts with a digit is unaffected, because the split is on the first
    colon and grep put its number before it.
    """
    numbers = set()
    for line in stdout.splitlines():
        head = line.split(":", 1)[0].strip()
        if head.isdigit():
            numbers.add(int(head))
    return frozenset(numbers)


def _sed_delimiter(pattern: str) -> str | None:
    for candidate in SED_DELIMITERS:
        if candidate not in pattern:
            return candidate
    return None


def _sed_argv(flag: str | None):
    def build(pattern: str, path: str) -> list[str] | None:
        delimiter = _sed_delimiter(pattern)
        if delimiter is None:
            return None
        argv = ["sed", "-n"]
        if flag:
            argv.append(flag)
        # \cPATc= prints the number of every line the address selects.
        argv += ["-e", f"\\{delimiter}{pattern}{delimiter}=", path]
        return argv

    return build


# awk's program text is fixed. The pattern arrives in the environment, so no
# amount of punctuation in it can become part of the program.
_AWK_PROGRAM = 'BEGIN{p=ENVIRON["REGEXDRIFT_PATTERN"]} $0 ~ p {print FNR}'

# The compile is guarded, because an uncaught `re.error` exits non-zero with a
# traceback and `first_complaint` reads the first line of stderr -- which for a
# traceback is "Traceback (most recent call last):". The status was right and the
# reason was not: nine engines say what they objected to, and this one reported
# the shape of its own failure. `regexdrift '\ycat\y'` printed
#
#     refused   grep -P: unrecognized character follows \
#     refused   python: Traceback (most recent call last):
#
# Exit 2 because that is the "could not" end of the grep convention, though this
# adapter is sed-style and reads any non-zero as a refusal.
_PYTHON_PROGRAM = (
    "import re,sys\n"
    "try:\n"
    "    p=re.compile(sys.argv[1])\n"
    "except re.error as exc:\n"
    "    print(exc, file=sys.stderr); sys.exit(2)\n"
    "for n,line in enumerate(open(sys.argv[2],encoding='utf-8',newline='').read().splitlines(),1):\n"
    "    if p.search(line): print(n)\n"
)

# Guarded for the same reason python's is, and it was missed because node's
# failure is louder rather than quieter. An uncaught `new RegExp` throws, and
# node's uncaught-exception report is sixteen lines that open with the location:
#
#     refused   node: [eval]:1
#
# which names nothing at all -- not the construct, not even that the subject was
# a regex. The reason is on line five, and lines two and three are this program
# text and a caret, so the report was also pasting regexdrift's own source into
# whatever read it. `err.message` is one line and says which construct:
# "Invalid regular expression: /(a/: Unterminated group".
#
# Exit 2 to match python's adapter, though both are sed-style here and any
# non-zero reads as a refusal.
_NODE_PROGRAM = (
    "const fs=require('fs');"
    "let re;"
    "try{re=new RegExp(process.argv[1])}"
    "catch(err){console.error(err.message);process.exit(2)}"
    "const text=fs.readFileSync(process.argv[2],'utf8');"
    "const lines=text.split('\\n');"
    "if(lines.length&&lines[lines.length-1]==='')lines.pop();"
    "lines.forEach((l,i)=>{if(re.test(l))console.log(i+1)});"
)


def _awk_env(pattern: str) -> dict[str, str]:
    return {"REGEXDRIFT_PATTERN": pattern}


ENGINES: list[Engine] = [
    Engine(
        name="grep",
        dialect="POSIX BRE (GNU grep)",
        binary="grep",
        _argv=lambda p, path: ["grep", "-n", "-e", p, path],
    ),
    Engine(
        name="grep -E",
        dialect="POSIX ERE (GNU grep)",
        binary="grep",
        _argv=lambda p, path: ["grep", "-E", "-n", "-e", p, path],
    ),
    Engine(
        name="grep -P",
        dialect="PCRE2",
        binary="grep",
        _argv=lambda p, path: ["grep", "-P", "-n", "-e", p, path],
    ),
    Engine(
        name="sed",
        dialect="POSIX BRE (GNU sed)",
        binary="sed",
        _argv=_sed_argv(None),
        grep_exit_convention=False,
    ),
    Engine(
        name="sed -E",
        dialect="POSIX ERE (GNU sed)",
        binary="sed",
        _argv=_sed_argv("-E"),
        grep_exit_convention=False,
    ),
    Engine(
        name="gawk",
        dialect="POSIX ERE + GNU extensions",
        binary="gawk",
        _argv=lambda p, path: ["gawk", _AWK_PROGRAM, path],
        _env=_awk_env,
        grep_exit_convention=False,
    ),
    Engine(
        name="mawk",
        dialect="POSIX ERE (mawk)",
        binary="mawk",
        _argv=lambda p, path: ["mawk", _AWK_PROGRAM, path],
        _env=_awk_env,
        grep_exit_convention=False,
    ),
    Engine(
        name="python",
        dialect="Python re",
        binary=sys.executable or "python3",
        _argv=lambda p, path: [sys.executable, "-c", _PYTHON_PROGRAM, p, path],
        grep_exit_convention=False,
    ),
    Engine(
        name="node",
        dialect="ECMAScript RegExp",
        binary="node",
        _argv=lambda p, path: ["node", "-e", _NODE_PROGRAM, p, path],
        grep_exit_convention=False,
    ),
    Engine(
        name="rg",
        dialect="Rust regex (RE2 family)",
        binary="rg",
        # --no-config so a RIPGREP_CONFIG_PATH in the environment cannot change
        # the answer, and the file is named explicitly so ignore rules do not
        # decide whether it gets read.
        _argv=lambda p, path: [
            "rg",
            "--no-config",
            "--color=never",
            "--no-heading",
            "--line-number",
            "--no-filename",
            "-e",
            p,
            path,
        ],
    ),
]

BY_NAME = {engine.name: engine for engine in ENGINES}


def select_engines(names: list[str] | None = None) -> list[Engine]:
    """The engines to ask, installed or not.

    Availability is deliberately *not* filtered here. A missing engine has to
    travel through the report as a missing engine, so it can be named in the
    output; dropping it at selection time is how "nine engines agree" quietly
    becomes a statement about four of them. `Engine.run` returns UNAVAILABLE for
    anything not on PATH, and `Comparison` keeps those separate from agreement.
    """
    return ENGINES if names is None else [BY_NAME[n] for n in names]


def installed(engines: list[Engine]) -> list[Engine]:
    """Those whose binary is on PATH. A PATH check, not a readiness check.

    Used only to answer "is it worth starting at all", where the cheap question
    is the right one: an engine that is on PATH but has no usable mode comes back
    UNAVAILABLE from `run` and is excluded from the comparison there, so it
    cannot reach a verdict either way.
    """
    return [engine for engine in engines if engine.available()]


def readiness(engines: list[Engine] | None = None) -> list[tuple[Engine, Readiness, str]]:
    """Ask every engine whether it can answer. One subprocess each, at most."""
    rows = []
    for engine in engines or ENGINES:
        state, detail = engine.readiness()
        rows.append((engine, state, detail))
    return rows


def run_all(pattern: str, path: str, engines: list[Engine] | None = None) -> list[Result]:
    return [engine.run(pattern, path) for engine in (engines or ENGINES)]
