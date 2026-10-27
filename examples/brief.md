# Cold review brief

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


Change: 4 hunks, diff hash ad72d9db248c7885

## H1  src/pay.py  (in def charge(order):)
_modified, +2 -1_
```diff
    10     total = order.total
-   11     if total < 0:
+   11     if total <= 0:
    12         raise ValueError("bad total")
+   13     retries = 0
    14     while retries < MAX:
    15         retries += 1
    16     return send(total)
```

## H2  src/pay.py  (in class Gateway:)
_modified, +1 -1_
```diff
    41     def ping(self):
-   41 -- old marker
+   42 --- new marker
    43     return True
```

## H3  README.md
_new file, +2 -0_
```diff
+    1 # Pay
+    2 hello
```

## H4  logo.png
_modified, +0 -0_
binary file: cannot be reviewed as text
