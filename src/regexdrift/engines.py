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
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum

TIMEOUT_SECONDS = 10

# Characters tried as a sed address delimiter, in order. None of them mean
# anything to a POSIX regex, so wrapping a pattern in one cannot change how the
# pattern is read -- and one absent from the pattern needs no escaping inside it.
SED_DELIMITERS = "%#,~@:;!_=<>|+&^"


class Status(Enum):
    """What came back from an engine.

    ACCEPTED covers "compiled the pattern and matched nothing", which is an
    answer. REJECTED is the engine saying no. UNAVAILABLE is nobody having
    looked, and is never folded into either of the others -- a tool that reports
    agreement because half the engines were missing is the failure this whole
    shop exists to find.
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Result:
    """One engine's answer about one pattern and one set of lines."""

    engine: str
    status: Status
    matched: frozenset[int] = frozenset()
    message: str = ""
    warning: str = ""

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
        return shutil.which(self.binary) is not None

    def run(self, pattern: str, path: str) -> Result:
        if not self.available():
            return Result(self.name, Status.UNAVAILABLE, message=f"{self.binary} is not on PATH")

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

_PYTHON_PROGRAM = (
    "import re,sys\n"
    "p=re.compile(sys.argv[1])\n"
    "for n,line in enumerate(open(sys.argv[2],encoding='utf-8',newline='').read().splitlines(),1):\n"
    "    if p.search(line): print(n)\n"
)

_NODE_PROGRAM = (
    "const fs=require('fs');"
    "const re=new RegExp(process.argv[1]);"
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
    return [engine for engine in engines if engine.available()]


def run_all(pattern: str, path: str, engines: list[Engine] | None = None) -> list[Result]:
    return [engine.run(pattern, path) for engine in (engines or ENGINES)]
