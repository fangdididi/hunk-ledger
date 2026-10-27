**English** | [简体中文](README.zh-CN.md)

# Hunk Ledger

**A pre-push code review where nothing gets skipped silently.**

Hunk Ledger is an agent skill for reviewing your own change (or your coding
agent's change) before it leaves your machine. It splits the change into
numbered hunks, has a fresh reviewer write a status for **every** hunk,
requires every finding to **quote the exact changed line** it accuses, sends
each finding through a second pass that tries to **refute** it, and ends with
a short receipt: `PASS`, `HOLD`, or `INCOMPLETE`.

Works with Claude Code, Codex, Cursor, Cline, Gemini CLI and any agent that can
read a `SKILL.md` and run Python.

## Why

Self-review by an agent tends to fail quietly:

- It reads the files that look interesting and never mentions the rest.
- It cites line numbers that do not exist, or paraphrases code into something it is not.
- It reviews with the same assumptions that wrote the bug.
- It reviews a diff that has already changed since.
- It "fixes" ten style nits nobody asked for and buries the one real problem.

Hunk Ledger makes each of these a hard failure the helper script reports,
instead of something you have to notice.

## Features

- **Coverage ledger**: every hunk must be `clean` (with what was checked), `finding`, or `skipped` (with why). Missing one makes the review `INCOMPLETE`.
- **Quote-checked findings**: each finding cites a file, line and side (`new` or `old`) and quotes the code; the script verifies the quote is really there, multi-line quotes included.
- **Find, then refute**: a verifier pass marks each finding `confirmed`, `refuted` or `unsure` with a reason. Refuted findings never block; unsure ones do.
- **Stop line**: you choose the lowest severity that blocks (`critical`, `major`, `minor`, `nit`). Findings below it are listed, not fixed.
- **Stale-review guard**: the ledger stores a hash of the diff; `receipt --recheck` refuses a review of a diff that moved.
- **Blast radius**: `callers` lists call sites outside the diff for functions the diff touches.
- **Whole change by default**: unpushed commits, uncommitted edits and untracked files, or any patch you pass in.
- **Hook-friendly exit codes**: 0 PASS, 1 HOLD, 2 INCOMPLETE. A sample `pre-push` hook is included.
- **No dependencies**: one Python 3.8+ file, standard library only, 68 tests.

## Install

Copy the folder into your agent's skills directory.

| Agent | Location |
|---|---|
| Claude Code | `~/.claude/skills/hunk-ledger/` (or `.claude/skills/` in a project) |
| Codex | `~/.codex/skills/hunk-ledger/` (or `.agents/skills/` in a project) |
| Cursor | `.cursor/skills/hunk-ledger/` in a project |
| Other agents | anywhere the agent reads skills from; or point it at `SKILL.md` |

```bash
git clone https://github.com/fangdididi/hunk-ledger.git ~/.claude/skills/hunk-ledger
```

Chinese instructions are in `SKILL.zh-CN.md`; use either file as the skill entry.

## Usage

Ask your agent:

> Review my change with hunk-ledger before I push.

Or drive the helper yourself:

```bash
S=~/.claude/skills/hunk-ledger/scripts/hunkledger.py

python3 $S split --out .hunk-ledger              # freeze the change -> ledger.json + brief.md
python3 $S callers .hunk-ledger/ledger.json      # call sites outside the diff
# reviewer writes .hunk-ledger/review.json (finder pass, then verifier pass)
python3 $S check .hunk-ledger/ledger.json .hunk-ledger/review.json
python3 $S receipt .hunk-ledger/ledger.json .hunk-ledger/review.json --block major --recheck
```

Options: `--base <ref>` to diff against a branch, `--diff file.patch` or
`--diff -` to review a patch, `--json` for machine-readable verdicts.

## Example

```
HUNK LEDGER RECEIPT  verdict: HOLD
diff ad72d9db248c7885  hunks 4  clean 2  finding 1  skipped 1  stop line: major and above

Blocking:
  F1 [major, confirmed] src/pay.py:11  zero-value orders (full coupons) now raise instead of completing

Below the stop line (do not fix unless asked):
  F2 [minor] src/pay.py:13  retries is reset inside charge(); the old module-level counter is now dead

Refuted by the verifier: F3

Not reviewed:
  H4 binary image, checked dimensions only
```

The agent shows this receipt as printed and asks which findings to fix. It
does not edit code before you answer, and it does not push on `HOLD` without
your say-so. The full example (diff, ledger, brief, review) is in `examples/`.

## File structure

```
hunk-ledger/
├── SKILL.md               # skill instructions (English)
├── SKILL.zh-CN.md         # skill instructions (Chinese)
├── README.md              # this file
├── README.zh-CN.md        # Chinese README
├── LICENSE                # MIT
├── scripts/
│   └── hunkledger.py      # split / check / receipt / callers
├── examples/
│   ├── change.diff        # sample change
│   ├── ledger.json        # its ledger
│   ├── brief.md           # its cold-reviewer brief
│   ├── review.json        # a finished review
│   ├── receipt.txt        # the printed receipt
│   └── pre-push.sh        # optional git hook
└── tests/
    ├── sample.diff
    └── test_hunkledger.py # python3 -m unittest discover -s tests
```

## License

MIT © fangdididi
