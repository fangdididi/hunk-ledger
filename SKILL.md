---
name: hunk-ledger
description: Review your own code change before you push it, with nothing skipped silently. Split the change into numbered hunks, have a fresh reviewer give every hunk a written status (clean, finding, or skipped with a reason), require every finding to quote the changed line it accuses, have a second pass try to refute each finding, then print a receipt with a PASS / HOLD / INCOMPLETE verdict. Use before git push, before opening a pull request, when asked to "review my change", or when an agent wrote code and you want an independent check before it leaves the machine.
---

# Hunk Ledger

## What it is

Hunk Ledger is a pre-push review routine for code written by you or by an
agent. Its one rule: **every changed hunk gets a written disposition, and every
accusation carries a quote that a script can check.**

Most self-reviews fail in three quiet ways:

1. **Silent coverage gaps.** The reviewer reads the interesting files and never
   looks at the rest. Nobody can tell what was not read.
2. **Invented evidence.** A finding names a line that does not exist, or
   paraphrases code that says something else.
3. **Author's blindness.** The agent that wrote the change reviews it with the
   same assumptions that produced the bug.

Hunk Ledger turns each failure into something a script refuses:

| Failure | What blocks it |
|---|---|
| Hunks nobody looked at | `check` fails until every hunk is `clean`, `finding` or `skipped` with a note |
| Made-up or misplaced quotes | `check` fails unless the quote appears at the cited line on the cited side of the diff |
| One-sided findings | every finding needs a verifier verdict (`confirmed`, `refuted`, `unsure`) with a reason |
| Reviewing yesterday's diff | `receipt --recheck` refuses a review whose diff hash no longer matches |
| Fixing things nobody asked for | findings below the stop line are listed, not fixed |

The helper `scripts/hunkledger.py` (Python 3.8+, standard library only) does the
bookkeeping. The judgement is still yours.

## When to use it

- Right before `git push` or opening a pull request.
- When the user says "review my change", "check this before I push", or "is this safe to ship".
- After an agent wrote or refactored code and you want a check that is independent of the session that wrote it.
- After fixing review findings, to confirm the new diff and not the old one.

Not for: reviewing someone else's open pull request comments (that is
triage of incoming feedback), whole-repository audits, or style-only passes.

## The verdicts

| Verdict | Exit code | Meaning |
|---|---|---|
| PASS | 0 | every hunk has a disposition, every finding is verified, nothing confirmed or unsure is at or above the stop line |
| HOLD | 1 | the review is complete and at least one confirmed or unsure finding is at or above the stop line |
| INCOMPLETE | 2 | the review itself is not done: missing hunks, unchecked quotes, unverified findings, or a stale diff |

`unsure` blocks like `confirmed`. If the verifier cannot decide, a human should.

## Severity scale

- **critical**: data loss, security hole, money or access wrong, crash on a main path.
- **major**: wrong behaviour a user will hit, broken contract with callers, missing error handling on a real path.
- **minor**: edge case unlikely in practice, misleading name that will cause a future bug, missing test for new logic.
- **nit**: taste. Never blocks unless the user set the stop line to `nit`.

The **stop line** is the lowest severity that blocks a push. Default `major`.
Ask the user once if the repository has no stated preference; remember the answer.

## Step by step

### 1. Freeze the change

```bash
python3 scripts/hunkledger.py split --out .hunk-ledger
```

With no `--diff`, the change is: commits not on the upstream branch, plus
uncommitted edits, plus untracked files (respecting `.gitignore`). Use
`--base main` to diff against a branch, or `--diff file.patch` / `--diff -` for
a patch. This writes:

- `.hunk-ledger/ledger.json`: numbered hunks (H1, H2, ...) with line numbers on both sides and a diff hash.
- `.hunk-ledger/brief.md`: a cold-reviewer brief containing only the diff and the answer format.

Add `.hunk-ledger/` to `.git/info/exclude` so it never gets committed.

If the change is large (roughly over 800 changed lines), say so and suggest
splitting the push. Review it anyway if the user insists, but mark it in the receipt.

### 2. Map the blast radius

```bash
python3 scripts/hunkledger.py callers .hunk-ledger/ledger.json
```

It lists call sites **outside** the diff for functions and methods the diff
defines or changes. A changed signature with twenty callers and none in the
diff is a lead worth chasing. The symbol detection is pattern based (Python,
JS/TS, Go, Rust, Java-like); treat it as a lead list, not proof.

Also run the project's own fast checks if they exist (linter, type checker,
unit tests touching the changed files). Their complaints are **candidates**:
they become findings only if you can quote the changed line responsible.

### 3. Finder pass in a fresh context

The reviewer should not be the context that wrote the code. In order of preference:

1. A separate subagent or a second model session that receives only `brief.md` and repository access.
2. A different agent product on the same machine.
3. If neither exists: yourself, after deliberately setting aside the conversation. Read only `brief.md` and the files. Mark findings `"by": "self"` and say so in the receipt.

For **each hunk**, read the hunk and the code around it, then ask:

- **Contract**: what do callers now get for empty, zero, null, negative, huge, or duplicate input?
- **Failure path**: what happens when the new call fails, times out, or half succeeds? Is an error swallowed?
- **State**: shared mutable state, ordering, retries, caches that now hold stale values.
- **Boundary**: does untrusted input reach a query, a path, a shell, a template, or an auth decision?
- **Deletion**: for every removed line, what was it protecting against, and who does that now?
- **Callers**: does the blast-radius list contain a caller this change breaks?
- **Tests**: does any test execute the changed line? If not, is that a finding (usually minor)?

Write `review.json`:

```json
{
  "hunks": {
    "H1": {"status": "finding"},
    "H2": {"status": "clean", "note": "rename only, all three callers updated"},
    "H3": {"status": "skipped", "note": "generated lockfile"}
  },
  "findings": [
    {"id": "F1", "hunk": "H1", "file": "src/pay.py", "line": 11, "side": "new",
     "quote": "if total <= 0:", "severity": "major", "category": "bug",
     "claim": "zero-value orders (full coupons) now raise instead of completing",
     "by": "finder"}
  ]
}
```

Rules for the finder:

- A `clean` note says what you checked, not "looks good".
- `skipped` is honest, not shameful: generated code, vendored code, binary files, lockfiles. Hand-written logic should almost never be skipped.
- Quote the code exactly. For a removed line, cite the old line number and set `"side": "old"`.
- One finding, one problem. Do not bundle.

### 4. Verifier pass: try to refute each finding

A second pass (again, ideally a different context) takes each finding and
tries to **prove it wrong**: read the callers, check the types, trace the
value, or write a two-line test. Then add:

```json
"verify": {"verdict": "confirmed", "reason": "checkout passes 0 for 100% coupons, see cart.py:88", "by": "verifier"}
```

- `refuted` findings stay in the receipt as refuted and never block.
- `unsure` blocks at or above the stop line. Say what would settle it.
- The verifier may lower or raise severity with a reason in the note.

### 5. Validate the review, not the code

```bash
python3 scripts/hunkledger.py check .hunk-ledger/ledger.json .hunk-ledger/review.json
```

Fix the **review file** until it is complete: add missing dispositions, fix
quotes, add verdicts. Do not touch the code yet.

### 6. Print the receipt and stop

```bash
python3 scripts/hunkledger.py receipt .hunk-ledger/ledger.json .hunk-ledger/review.json --block major --recheck
```

Show the receipt to the user **as printed**. Then ask one question:
"Fix which ones? All blocking, a list of IDs, or none?" Do not change code
before the answer, unless the user already said "review and fix".

### 7. Fix, then re-freeze

Fix only the findings the user named. The diff now moved, so the old review is
stale by design. Run `split` again and review again. You may reuse a `clean`
note for a hunk whose text is identical to before, but every hunk must be
re-listed.

### 8. Push rule

- PASS: push is fine.
- HOLD: do not push unless the user, having seen the blocking findings, says to.
- INCOMPLETE: never present it as a review. Say exactly what is missing.

## Example

A change touches a payment function, a README and a logo:

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

The agent shows this, asks "Fix F1 only, or F1 and F2?", and waits.
The verifier refuted F3 (it claimed negatives were no longer rejected; `<= 0` still rejects them),
so F3 is listed but never blocks. See `examples/` for the matching diff, ledger, brief and `review.json`.

## Tips

- **Small pushes review better.** If the ledger has more than ~40 hunks, propose splitting the push.
- **Deletions hide the worst bugs.** A removed `if` or `try` deserves a sentence even when the hunk looks trivial.
- **"Unsure" is a valid answer.** It is better than a confident guess in either direction.
- **Scanner noise is not a finding.** No quote, no finding.
- **Keep the stop line stable** for a repository; moving it per push to get a PASS defeats the point.
- **Do not grade your own homework twice.** If the same context found and verified a finding, the receipt warns. Mention it to the user.
- **Exit codes are for hooks.** A `pre-push` hook can run `receipt --recheck` and refuse on 1 or 2.
