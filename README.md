# regexdrift

Give it a regex and it runs every regex engine on your machine against the same
input, then tells you which ones disagreed. For anyone who has moved a pattern
from `grep` into `awk`, or out of Python into a shell script, and wants to know
before shipping it whether it still means the same thing.

It is useful because the answer is almost never an error. Engines that disagree
about a pattern exit 0 and hand back different lines.

## The case it was built for

This is a real line from oh-my-zsh, `plugins/battery/battery.plugin.zsh:31`
(at `74965c9`, 2026-09-23), which reads the battery percentage on macOS:

```sh
pmset -g batt | grep -Eo "\d+%" | cut -d% -f1
```

```console
$ regexdrift '\d+%'
pattern  \d+%
input    7 generated probes — pass a FILE or - to use your own

3 behaviours among the 9 engines that accepted it.

  group 1   grep · sed
            POSIX BRE (GNU grep), POSIX BRE (GNU sed)
            matched, of the disputed lines: `\d+%`  `d+%`

  group 2   grep -E · sed -E · gawk · mawk
            POSIX ERE (GNU grep), POSIX ERE (GNU sed), POSIX ERE + GNU extensions, POSIX ERE (mawk)
            matched, of the disputed lines: `ddd%`  `d%`

  group 3   grep -P · python · node
            PCRE2, Python re, ECMAScript RegExp
            matched, of the disputed lines: `777%`  `7%`

  warned    gawk: regexp escape sequence `\d' is not a known regexp operator
  not run   rg (not installed)

why they might differ
  · \d belongs to PCRE, Python and ECMAScript. GNU grep, sed and awk added \w
    and \s as extensions and never added \d, so there it is simply the letter
    d -- grep -Eo '\d+%' looks for a run of d's followed by a percent sign. Of
    all of them only gawk warns.
```

`grep -E` is group 2. It is looking for one or more letter `d`s before a percent
sign, and `pmset` output contains no such thing:

```console
$ pmset -g batt | grep -Eo '\d+%'
$ echo $?
1
$ pmset -g batt | grep -Eo '[0-9]+%'
100%
```

So `battery_pct` returns an empty string. Five lines higher in the same file,
`get_charger_power` uses `'[0-9]\+'` and works. Nothing about the failure says
"regex": no error, no warning, exit 1 like any other search that found nothing.

## Install

Python 3.10 or newer, no dependencies.

```sh
git clone https://github.com/committed-nightly/regexdrift
cd regexdrift
pip install .
```

## Usage

```sh
regexdrift PATTERN            # generate probe input
regexdrift PATTERN FILE       # use a real corpus
regexdrift PATTERN -          # read the corpus from stdin
regexdrift --list-engines     # what is installed here
```

Against a real file, the report gives line numbers and shows only the lines the
engines actually disagree about:

```console
$ regexdrift '\bTODO\b' src/main.c
```

Exit codes, so it can fail a build:

| code | meaning |
| --- | --- |
| 0 | every engine that accepted the pattern matched the same lines |
| 1 | they did not |
| 2 | fewer than two engines could answer, so nothing was compared |

`--strict` also fails on an engine refusing the pattern or warning about it.
`--json` for machine-readable output. `--engine NAME` to narrow it down, which is
the flag to reach for when the real question is "does this work in *awk*".

## Engines

Whatever is on your `PATH`, from this list:

| name | dialect |
| --- | --- |
| `grep` | POSIX BRE (GNU grep) |
| `grep -E` | POSIX ERE (GNU grep) |
| `grep -P` | PCRE2 |
| `sed` | POSIX BRE (GNU sed) |
| `sed -E` | POSIX ERE (GNU sed) |
| `gawk` | POSIX ERE + GNU extensions |
| `mawk` | POSIX ERE (mawk) |
| `python` | Python `re`, the interpreter you installed this with |
| `node` | ECMAScript `RegExp` |
| `rg` | Rust `regex` (RE2 family) |

Missing engines are named in the report rather than dropped from it. "Nine
engines agreed" quietly becoming a statement about four of them is the failure
this tool is otherwise about.

## Three things it is opinionated about

**It never implements a regex.** Every claim comes from running a real engine and
reading back which line numbers matched. A second opinion, hand-written from the
standards, would disagree with a real engine somewhere — and being confidently
wrong about exactly this is the one thing the tool cannot afford.

**A refusal is not a disagreement.** ripgrep rejecting a backreference exits 2 and
prints a parse error; you will find out. The drift in the name is the quiet kind,
where every engine exits 0 and two of them return different lines. Refusals are
reported in full and do not set the exit code unless you pass `--strict`.

**The explanations only appear once a difference has been measured.** There is a
short list of notes about `\d`, backreferences, lazy quantifiers and the rest, and
none of them can print unless the engines were just observed to disagree. A tool
that printed "`\d` is not portable" from reading the pattern would be a lint rule,
and it would fire constantly on patterns that are completely fine because every
engine that will ever run them agrees.

## Probes are guesses; results are not

With no file, input has to be invented. The pattern is read several ways on
purpose — escapes as character classes and as bare letters, quantifiers as
operators and as literal punctuation, each branch of an alternation on its own —
because that is exactly what the engines do to each other. All of it is then fed
to every engine for real.

A wrong guess costs a useless probe and cannot produce a wrong finding. But it can
hide a true one, so a clean exit in probe mode is reported as "agreed on these 7
probes" and never as "the engines agree". If you have a corpus, use it.

## What it does not do

- **No BSD tooling.** macOS `grep` and `sed` are a different implementation with
  their own gaps, and are much of why `\d` in a shell script is a bad idea. This
  reports what is installed, so on Linux it cannot speak for them.
- **One line at a time.** Every engine here is run in a line-oriented mode, so
  patterns spanning newlines are out of scope.
- **No opinion on which engine is right.** It tells you they differ. Which one you
  meant is your business.
- **It does not read your repository.** Extracting patterns out of shell scripts
  and checking them in bulk is the obvious next thing and is not here.

## Licence

MIT.
