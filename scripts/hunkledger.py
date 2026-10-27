#!/usr/bin/env python3
"""hunkledger: give every changed hunk a written disposition before you push.

Commands
  split    turn a unified diff into a numbered hunk ledger plus a cold-reviewer brief
  check    validate a review file against the ledger (coverage, quotes, verdicts)
  receipt  print the push verdict (PASS / HOLD / INCOMPLETE) and exit 0 / 1 / 2
  callers  list call sites outside the diff for symbols the diff touches

Standard library only. Python 3.8+.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

SEVERITIES = ["critical", "major", "minor", "nit"]
STATUSES = ["clean", "finding", "skipped"]
VERDICTS = ["confirmed", "refuted", "unsure"]
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


# ---------------------------------------------------------------- diff parsing

def _strip_prefix(path):
    if path == "/dev/null":
        return None
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path[:2] in ("a/", "b/"):
        return path[2:]
    return path


def parse_diff(text):
    """Return a list of hunk dicts from unified diff text."""
    hunks = []
    cur_file = None
    old_path = new_path = None
    cur = None
    old_no = new_no = 0

    def close():
        nonlocal cur
        if cur is not None:
            hunks.append(cur)
        cur = None

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            close()
            m = re.match(r"diff --git (\S+) (\S+)$", raw)
            old_path = _strip_prefix(m.group(1)) if m else None
            new_path = _strip_prefix(m.group(2)) if m else None
            cur_file = {"old": old_path, "new": new_path}
            continue
        if cur_file is None and (raw.startswith("--- ") or raw.startswith("+++ ")):
            cur_file = {"old": None, "new": None}
        if raw.startswith("--- ") and (cur is None or cur.get("_done")):
            close()
            cur_file["old"] = _strip_prefix(raw[4:].split("\t")[0])
            continue
        if raw.startswith("+++ ") and (cur is None or cur.get("_done")):
            close()
            cur_file["new"] = _strip_prefix(raw[4:].split("\t")[0])
            continue
        if raw.startswith("Binary files ") and cur_file is not None:
            close()
            path = cur_file.get("new") or cur_file.get("old")
            hunks.append({"file": path, "binary": True, "old_start": 0, "new_start": 0,
                          "header": "", "lines": [], "status_hint": _file_state(cur_file)})
            continue
        m = HUNK_RE.match(raw)
        if m and cur_file is not None:
            close()
            old_no = int(m.group(1))
            new_no = int(m.group(3))
            path = cur_file.get("new") or cur_file.get("old")
            cur = {"file": path, "binary": False, "old_start": old_no, "new_start": new_no,
                   "header": m.group(5).strip(), "lines": [],
                   "status_hint": _file_state(cur_file),
                   "_left_old": int(m.group(2)) if m.group(2) is not None else 1,
                   "_left_new": int(m.group(4)) if m.group(4) is not None else 1}
            continue
        if cur is None:
            continue
        if raw.startswith("\\"):
            continue
        if cur["_left_old"] <= 0 and cur["_left_new"] <= 0:
            cur["_done"] = True
            continue
        tag, body = (raw[:1], raw[1:]) if raw else (" ", "")
        if tag == "+":
            cur["lines"].append(["+", None, new_no, body]); new_no += 1; cur["_left_new"] -= 1
        elif tag == "-":
            cur["lines"].append(["-", old_no, None, body]); old_no += 1; cur["_left_old"] -= 1
        elif tag == " ":
            cur["lines"].append([" ", old_no, new_no, body]); old_no += 1; new_no += 1
            cur["_left_old"] -= 1; cur["_left_new"] -= 1
        else:
            cur["_done"] = True
        if cur["_left_old"] <= 0 and cur["_left_new"] <= 0:
            cur["_done"] = True
    close()
    for i, h in enumerate(hunks, 1):
        h["id"] = "H%d" % i
        for k in [k for k in h if k.startswith("_")]:
            del h[k]
        h["added"] = sum(1 for l in h["lines"] if l[0] == "+")
        h["removed"] = sum(1 for l in h["lines"] if l[0] == "-")
    return hunks


def _file_state(f):
    if f.get("old") is None:
        return "new file"
    if f.get("new") is None:
        return "deleted file"
    if f.get("old") != f.get("new"):
        return "renamed"
    return "modified"


def untracked_as_diff(paths, root="."):
    out = []
    for p in paths:
        full = os.path.join(root, p)
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        out.append("diff --git a/%s b/%s" % (p, p))
        out.append("--- /dev/null")
        out.append("+++ b/%s" % p)
        if b"\0" in data[:8000]:
            out.append("Binary files /dev/null and b/%s differ" % p)
            continue
        lines = data.decode("utf-8", "replace").splitlines()
        if not lines:
            continue
        out.append("@@ -0,0 +1,%d @@" % len(lines))
        out.extend("+" + l for l in lines)
    return "\n".join(out) + ("\n" if out else "")


def _git(args, cwd="."):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def collect_git_diff(base=None, cwd="."):
    """The change = commits not on the upstream (or base) + uncommitted + untracked."""
    if base is None:
        try:
            base = _git(["merge-base", "HEAD", "@{upstream}"], cwd).strip()
        except RuntimeError:
            base = "HEAD"
    text = _git(["diff", "--no-color", "--no-ext-diff", "-M", base], cwd)
    untracked = [p for p in _git(["ls-files", "--others", "--exclude-standard"], cwd).splitlines() if p]
    return text + untracked_as_diff(untracked, cwd), base


def diff_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- brief

BRIEF_HEAD = """# Cold review brief

You are reviewing a code change you did not write and have no history with.
Read only what is below plus the repository itself. Do not trust commit
messages, chat logs, or the author's explanation; judge the code.

For EVERY hunk id below, record exactly one status in review.json:
- clean   : you read it and found nothing; the note says what you checked
- finding : at least one finding points at this hunk
- skipped : you did not review it; the note says why (generated, vendored, binary...)

Every finding must quote the changed code verbatim (one or more consecutive
lines, trimmed is fine) and cite the line number on the side it lives on
(new side for + and context lines, old side for removed lines: side "old").
Severity is one of: critical, major, minor, nit.

Shape of review.json:
{
  "hunks": {"H1": {"status": "clean", "note": "checked null path and loop bound"}},
  "findings": [
    {"id": "F1", "hunk": "H2", "file": "src/app.py", "line": 41, "side": "new",
     "quote": "if retries > max_retries:", "severity": "major",
     "category": "bug", "claim": "off by one: allows max_retries + 1 attempts",
     "verify": {"verdict": "confirmed", "reason": "loop starts at 0 and checks after the call"}}
  ]
}
Leave "verify" out in the finder pass; a separate verifier fills it in.
"""


def render_brief(ledger):
    out = [BRIEF_HEAD, "", "Change: %d hunks, diff hash %s" % (len(ledger["hunks"]), ledger["diff_hash"]), ""]
    for h in ledger["hunks"]:
        title = "## %s  %s" % (h["id"], h["file"])
        if h.get("header"):
            title += "  (in %s)" % h["header"]
        out.append(title)
        out.append("_%s, +%d -%d_" % (h.get("status_hint", ""), h["added"], h["removed"]))
        if h["binary"]:
            out.append("binary file: cannot be reviewed as text")
            out.append("")
            continue
        out.append("```diff")
        for tag, o, n, body in h["lines"]:
            num = n if n is not None else o
            out.append("%s%5s %s" % (tag, num, body))
        out.append("```")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- checking

def _norm(s):
    return " ".join(s.split())


def quote_matches(hunk, line, quote, side="new"):
    parts = [_norm(p) for p in quote.splitlines() if _norm(p)]
    if not parts:
        return False
    idx = 2 if side == "new" else 1
    rows = [l for l in hunk["lines"] if l[idx] is not None]
    for i, row in enumerate(rows):
        if row[idx] != line:
            continue
        if i + len(parts) > len(rows):
            return False
        return all(p in _norm(rows[i + k][3]) for k, p in enumerate(parts))
    return False


def check_review(ledger, review):
    """Return (errors, warnings). Errors make the review incomplete."""
    errors, warnings = [], []
    hunks = {h["id"]: h for h in ledger["hunks"]}
    disp = review.get("hunks") or {}
    findings = review.get("findings") or []
    by_hunk = {}
    seen_ids = set()
    for hid in hunks:
        d = disp.get(hid)
        if not d:
            errors.append("%s has no disposition (every hunk needs clean, finding or skipped)" % hid)
            continue
        st = d.get("status")
        if st not in STATUSES:
            errors.append("%s has unknown status %r" % (hid, st))
        elif st in ("clean", "skipped") and not (d.get("note") or "").strip():
            errors.append("%s is %s but has no note saying what was checked or why it was skipped" % (hid, st))
    for hid in disp:
        if hid not in hunks:
            errors.append("disposition for %s, which is not in the ledger (stale review?)" % hid)
    for i, f in enumerate(findings, 1):
        fid = f.get("id") or "finding#%d" % i
        if fid in seen_ids:
            errors.append("%s: duplicate finding id" % fid)
        seen_ids.add(fid)
        hid = f.get("hunk")
        h = hunks.get(hid)
        if h is None:
            errors.append("%s points at unknown hunk %r" % (fid, hid))
            continue
        by_hunk.setdefault(hid, []).append(f)
        if f.get("file") and f["file"] != h["file"]:
            errors.append("%s says file %s but %s is in %s" % (fid, f["file"], hid, h["file"]))
        if f.get("severity") not in SEVERITIES:
            errors.append("%s has severity %r (use %s)" % (fid, f.get("severity"), ", ".join(SEVERITIES)))
        if not (f.get("claim") or "").strip():
            errors.append("%s has no claim" % fid)
        side = f.get("side", "new")
        if side not in ("new", "old"):
            errors.append("%s has side %r (use new or old)" % (fid, side))
        elif h["binary"]:
            errors.append("%s quotes a binary hunk" % fid)
        elif not isinstance(f.get("line"), int) or not quote_matches(h, f["line"], f.get("quote") or "", side):
            errors.append("%s: quote not found at %s line %s (%s side); findings must quote the diff verbatim"
                          % (fid, h["file"], f.get("line"), side))
        v = f.get("verify")
        if not v:
            errors.append("%s has not been verified (verify.verdict missing)" % fid)
        else:
            if v.get("verdict") not in VERDICTS:
                errors.append("%s has verdict %r (use %s)" % (fid, v.get("verdict"), ", ".join(VERDICTS)))
            if not (v.get("reason") or "").strip():
                errors.append("%s verdict has no reason" % fid)
            if v.get("by") and f.get("by") and v["by"] == f["by"]:
                warnings.append("%s was found and verified by the same reviewer (%s)" % (fid, v["by"]))
    for hid, d in disp.items():
        if hid in hunks and d.get("status") == "finding" and hid not in by_hunk:
            errors.append("%s is marked finding but no finding points at it" % hid)
        if hid in hunks and d.get("status") in ("clean", "skipped") and hid in by_hunk:
            errors.append("%s is marked %s but has findings" % (hid, d.get("status")))
    reviewed_lines = sum(hunks[h]["added"] + hunks[h]["removed"] for h in hunks
                         if (disp.get(h) or {}).get("status") in ("clean", "finding"))
    total_lines = sum(h["added"] + h["removed"] for h in hunks.values()) or 1
    skipped = [h for h in hunks if (disp.get(h) or {}).get("status") == "skipped"]
    if skipped and reviewed_lines / total_lines < 0.5:
        warnings.append("more than half of the changed lines were skipped")
    return errors, warnings


def decide(ledger, review, block="major", current_hash=None):
    errors, warnings = check_review(ledger, review)
    if current_hash and current_hash != ledger["diff_hash"]:
        errors.insert(0, "the change moved since the ledger was made (hash %s now %s); split and review again"
                      % (ledger["diff_hash"], current_hash))
    limit = SEVERITIES.index(block)
    blocking, notes = [], []
    for f in review.get("findings") or []:
        v = (f.get("verify") or {}).get("verdict")
        if v == "refuted" or f.get("severity") not in SEVERITIES:
            continue
        if SEVERITIES.index(f["severity"]) <= limit:
            blocking.append(f)
        else:
            notes.append(f)
    if errors:
        verdict, code = "INCOMPLETE", 2
    elif blocking:
        verdict, code = "HOLD", 1
    else:
        verdict, code = "PASS", 0
    return {"verdict": verdict, "exit": code, "errors": errors, "warnings": warnings,
            "blocking": blocking, "below_line": notes}


def render_receipt(ledger, review, result, block):
    disp = review.get("hunks") or {}
    counts = {s: 0 for s in STATUSES}
    for h in ledger["hunks"]:
        st = (disp.get(h["id"]) or {}).get("status")
        if st in counts:
            counts[st] += 1
    lines = ["HUNK LEDGER RECEIPT  verdict: %s" % result["verdict"],
             "diff %s  hunks %d  clean %d  finding %d  skipped %d  stop line: %s and above"
             % (ledger["diff_hash"], len(ledger["hunks"]), counts["clean"], counts["finding"], counts["skipped"], block)]
    if result["errors"]:
        lines.append("")
        lines.append("Not a complete review:")
        lines += ["  - " + e for e in result["errors"]]
    if result["blocking"]:
        lines.append("")
        lines.append("Blocking:")
        for f in result["blocking"]:
            v = (f.get("verify") or {}).get("verdict", "?")
            lines.append("  %s [%s, %s] %s:%s  %s" % (f.get("id"), f["severity"], v, f.get("file"), f.get("line"), f.get("claim", "")))
    if result["below_line"]:
        lines.append("")
        lines.append("Below the stop line (do not fix unless asked):")
        for f in result["below_line"]:
            lines.append("  %s [%s] %s:%s  %s" % (f.get("id"), f["severity"], f.get("file"), f.get("line"), f.get("claim", "")))
    refuted = [f for f in review.get("findings") or [] if (f.get("verify") or {}).get("verdict") == "refuted"]
    if refuted:
        lines.append("")
        lines.append("Refuted by the verifier: " + ", ".join(f.get("id", "?") for f in refuted))
    skipped = [(h, (disp.get(h) or {}).get("note", "")) for h in (x["id"] for x in ledger["hunks"])
               if (disp.get(h) or {}).get("status") == "skipped"]
    if skipped:
        lines.append("")
        lines.append("Not reviewed:")
        lines += ["  %s %s" % (h, n) for h, n in skipped]
    if result["warnings"]:
        lines.append("")
        lines += ["warning: " + w for w in result["warnings"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- callers

SYMBOL_RES = [
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:public|private|protected|static|\s)*[\w<>\[\], ]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?\s*$"),
]
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__", "target", ".hunk-ledger"}
KEYWORDS = {"if", "for", "while", "switch", "return", "catch", "new", "else", "main"}


def symbols_in(hunk):
    names = set()
    candidates = [hunk.get("header", "")] + [l[3] for l in hunk["lines"] if l[0] in "+-"]
    for text in candidates:
        for rx in SYMBOL_RES:
            m = rx.match(text)
            if m and m.group(1) not in KEYWORDS and len(m.group(1)) > 2:
                names.add(m.group(1))
                break
    return names


def find_callers(ledger, root=".", limit=200):
    changed_files = {h["file"] for h in ledger["hunks"]}
    symbols = set()
    for h in ledger["hunks"]:
        if not h["binary"]:
            symbols |= symbols_in(h)
    if not symbols:
        return {}
    rx = re.compile(r"\b(" + "|".join(sorted(map(re.escape, symbols))) + r")\s*\(")
    hits = {s: [] for s in symbols}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel in changed_files:
                continue
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    for no, text in enumerate(fh, 1):
                        for m in rx.finditer(text):
                            if len(hits[m.group(1)]) < limit:
                                hits[m.group(1)].append("%s:%d" % (rel, no))
            except (UnicodeDecodeError, OSError):
                continue
    return hits


# ---------------------------------------------------------------- cli

def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_diff(args):
    if args.diff == "-":
        return sys.stdin.read(), "stdin"
    if args.diff:
        with open(args.diff, encoding="utf-8") as fh:
            return fh.read(), args.diff
    text, base = collect_git_diff(args.base)
    return text, "git diff " + base


def cmd_split(args):
    text, source = _read_diff(args)
    hunks = parse_diff(text)
    ledger = {"source": source, "diff_hash": diff_hash(text), "hunks": hunks}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "ledger.json"), "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=1)
    with open(os.path.join(args.out, "brief.md"), "w", encoding="utf-8") as fh:
        fh.write(render_brief(ledger))
    files = sorted({h["file"] for h in hunks})
    print("%d hunks in %d files, diff hash %s" % (len(hunks), len(files), ledger["diff_hash"]))
    print("wrote %s/ledger.json and %s/brief.md" % (args.out, args.out))
    return 0 if hunks else 3


def cmd_check(args):
    errors, warnings = check_review(_load(args.ledger), _load(args.review))
    for e in errors:
        print("ERROR " + e)
    for w in warnings:
        print("WARN  " + w)
    if not errors:
        print("review file is complete")
    return 2 if errors else 0


def cmd_receipt(args):
    ledger, review = _load(args.ledger), _load(args.review)
    current = None
    if args.recheck:
        text, _ = _read_diff(args)
        current = diff_hash(text)
    result = decide(ledger, review, args.block, current)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(render_receipt(ledger, review, result, args.block))
    return result["exit"]


def cmd_callers(args):
    hits = find_callers(_load(args.ledger), args.root)
    if not hits:
        print("no changed function or method names recognised")
        return 0
    for sym in sorted(hits):
        where = hits[sym]
        print("%s: %d call site(s) outside the diff" % (sym, len(where)))
        for w in where[:args.show]:
            print("   " + w)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="hunkledger", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def diff_opts(sp):
        sp.add_argument("--diff", help="unified diff file, or - for stdin (default: run git)")
        sp.add_argument("--base", help="git ref to diff against (default: merge-base with upstream, else HEAD)")

    s = sub.add_parser("split", help="make ledger.json and brief.md from the change")
    diff_opts(s)
    s.add_argument("--out", default=".hunk-ledger")
    s.set_defaults(func=cmd_split)

    c = sub.add_parser("check", help="validate review.json against ledger.json")
    c.add_argument("ledger"); c.add_argument("review")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("receipt", help="print the verdict; exit 0 PASS, 1 HOLD, 2 INCOMPLETE")
    r.add_argument("ledger"); r.add_argument("review")
    r.add_argument("--block", default="major", choices=SEVERITIES, help="lowest severity that blocks a push")
    r.add_argument("--recheck", action="store_true", help="re-read the change and refuse a stale review")
    r.add_argument("--json", action="store_true")
    diff_opts(r)
    r.set_defaults(func=cmd_receipt)

    k = sub.add_parser("callers", help="call sites outside the diff for touched symbols")
    k.add_argument("ledger"); k.add_argument("--root", default=".")
    k.add_argument("--show", type=int, default=10)
    k.set_defaults(func=cmd_callers)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as e:
        print("hunkledger: " + str(e), file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
