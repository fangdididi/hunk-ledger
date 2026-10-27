import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import hunkledger as hl  # noqa: E402

SAMPLE = open(os.path.join(HERE, "sample.diff"), encoding="utf-8").read()


def ledger_of(text=SAMPLE):
    return {"source": "test", "diff_hash": hl.diff_hash(text), "hunks": hl.parse_diff(text)}


def good_review():
    return {
        "hunks": {
            "H1": {"status": "finding"},
            "H2": {"status": "clean", "note": "marker text only"},
            "H3": {"status": "clean", "note": "docs"},
            "H4": {"status": "skipped", "note": "binary image"},
        },
        "findings": [
            {"id": "F1", "hunk": "H1", "file": "src/pay.py", "line": 11, "quote": "if total <= 0:",
             "severity": "major", "category": "bug", "claim": "zero totals now raise",
             "by": "finder", "verify": {"verdict": "confirmed", "reason": "free orders exist", "by": "verifier"}},
        ],
    }


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.h = hl.parse_diff(SAMPLE)

    def test_hunk_count(self):
        self.assertEqual(len(self.h), 4)

    def test_ids_are_sequential(self):
        self.assertEqual([x["id"] for x in self.h], ["H1", "H2", "H3", "H4"])

    def test_file_paths_strip_prefix(self):
        self.assertEqual(self.h[0]["file"], "src/pay.py")

    def test_header_kept(self):
        self.assertEqual(self.h[0]["header"], "def charge(order):")

    def test_added_removed_counts(self):
        self.assertEqual((self.h[0]["added"], self.h[0]["removed"]), (2, 1))

    def test_new_side_numbers(self):
        plus = [l for l in self.h[0]["lines"] if l[0] == "+"]
        self.assertEqual([l[2] for l in plus], [11, 13])

    def test_old_side_numbers(self):
        minus = [l for l in self.h[0]["lines"] if l[0] == "-"]
        self.assertEqual(minus[0][1], 11)

    def test_line_starting_with_dashes_inside_hunk(self):
        self.assertEqual(self.h[1]["lines"][1], ["-", 41, None, "-- old marker"])
        self.assertEqual(self.h[1]["lines"][2], ["+", None, 42, "--- new marker"])

    def test_new_file_state(self):
        self.assertEqual(self.h[2]["status_hint"], "new file")

    def test_binary_hunk(self):
        self.assertTrue(self.h[3]["binary"])
        self.assertEqual(self.h[3]["file"], "logo.png")

    def test_no_internal_keys_leak(self):
        for h in self.h:
            self.assertFalse([k for k in h if k.startswith("_")])

    def test_deleted_file(self):
        d = "diff --git a/x.txt b/x.txt\ndeleted file mode 100644\n--- a/x.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
        h = hl.parse_diff(d)
        self.assertEqual(h[0]["file"], "x.txt")
        self.assertEqual(h[0]["status_hint"], "deleted file")
        self.assertEqual(h[0]["removed"], 2)

    def test_rename(self):
        d = "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        h = hl.parse_diff(d)
        self.assertEqual(h[0]["file"], "new.py")
        self.assertEqual(h[0]["status_hint"], "renamed")

    def test_no_newline_marker_ignored(self):
        d = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file\n"
        h = hl.parse_diff(d)
        self.assertEqual((h[0]["added"], h[0]["removed"]), (1, 1))

    def test_plain_diff_without_git_header(self):
        d = "--- a/f.c\n+++ b/f.c\n@@ -3,2 +3,3 @@ int main()\n int x;\n+int y;\n return 0;\n"
        h = hl.parse_diff(d)
        self.assertEqual(h[0]["file"], "f.c")
        self.assertEqual(h[0]["added"], 1)

    def test_two_hunks_same_file(self):
        self.assertEqual(self.h[0]["file"], self.h[1]["file"])

    def test_empty_diff(self):
        self.assertEqual(hl.parse_diff(""), [])

    def test_untracked_as_diff(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "n.py"), "w").write("a = 1\nb = 2\n")
            h = hl.parse_diff(hl.untracked_as_diff(["n.py"], d))
            self.assertEqual(h[0]["added"], 2)
            self.assertEqual(h[0]["status_hint"], "new file")

    def test_untracked_binary(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "b.bin"), "wb").write(b"\0\1\2")
            h = hl.parse_diff(hl.untracked_as_diff(["b.bin"], d))
            self.assertTrue(h[0]["binary"])


class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.h = hl.parse_diff(SAMPLE)[0]

    def test_exact(self):
        self.assertTrue(hl.quote_matches(self.h, 11, "if total <= 0:"))

    def test_whitespace_collapsed(self):
        self.assertTrue(hl.quote_matches(self.h, 11, "  if   total <=  0:  "))

    def test_wrong_line(self):
        self.assertFalse(hl.quote_matches(self.h, 12, "if total <= 0:"))

    def test_invented_text(self):
        self.assertFalse(hl.quote_matches(self.h, 11, "if total < 1:"))

    def test_multiline(self):
        self.assertTrue(hl.quote_matches(self.h, 11, "if total <= 0:\nraise ValueError"))

    def test_multiline_breaks(self):
        self.assertFalse(hl.quote_matches(self.h, 11, "if total <= 0:\nretries = 0"))

    def test_old_side(self):
        self.assertTrue(hl.quote_matches(self.h, 11, "if total < 0:", side="old"))

    def test_old_side_text_not_on_new_side(self):
        self.assertFalse(hl.quote_matches(self.h, 11, "if total < 0:", side="new"))

    def test_empty_quote(self):
        self.assertFalse(hl.quote_matches(self.h, 11, "   "))

    def test_context_line_quotable(self):
        self.assertTrue(hl.quote_matches(self.h, 10, "total = order.total"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.ledger = ledger_of()

    def test_good_review_has_no_errors(self):
        e, w = hl.check_review(self.ledger, good_review())
        self.assertEqual(e, [])

    def test_missing_disposition(self):
        r = good_review(); del r["hunks"]["H3"]
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("H3 has no disposition" in x for x in e))

    def test_clean_needs_note(self):
        r = good_review(); r["hunks"]["H2"] = {"status": "clean"}
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("H2 is clean" in x for x in e))

    def test_skipped_needs_note(self):
        r = good_review(); r["hunks"]["H4"] = {"status": "skipped", "note": " "}
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("H4 is skipped" in x for x in e))

    def test_unknown_status(self):
        r = good_review(); r["hunks"]["H2"] = {"status": "looks fine", "note": "x"}
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("unknown status" in x for x in e))

    def test_unknown_hunk_in_disposition(self):
        r = good_review(); r["hunks"]["H9"] = {"status": "clean", "note": "x"}
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("H9" in x and "not in the ledger" in x for x in e))

    def test_finding_status_without_finding(self):
        r = good_review(); r["findings"] = []
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("marked finding" in x for x in e))

    def test_clean_with_findings(self):
        r = good_review(); r["hunks"]["H1"] = {"status": "clean", "note": "x"}
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("marked clean but has findings" in x for x in e))

    def test_bad_quote(self):
        r = good_review(); r["findings"][0]["quote"] = "if total == 0:"
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("quote not found" in x for x in e))

    def test_bad_severity(self):
        r = good_review(); r["findings"][0]["severity"] = "high"
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("severity" in x for x in e))

    def test_missing_verify(self):
        r = good_review(); del r["findings"][0]["verify"]
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("not been verified" in x for x in e))

    def test_verify_needs_reason(self):
        r = good_review(); r["findings"][0]["verify"] = {"verdict": "refuted"}
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("no reason" in x for x in e))

    def test_bad_verdict(self):
        r = good_review(); r["findings"][0]["verify"] = {"verdict": "maybe", "reason": "x"}
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("verdict 'maybe'" in x for x in e))

    def test_wrong_file(self):
        r = good_review(); r["findings"][0]["file"] = "src/other.py"
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("says file" in x for x in e))

    def test_unknown_hunk_in_finding(self):
        r = good_review(); r["findings"][0]["hunk"] = "H77"
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("unknown hunk" in x for x in e))

    def test_duplicate_finding_id(self):
        r = good_review(); r["findings"].append(dict(r["findings"][0]))
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("duplicate" in x for x in e))

    def test_binary_cannot_be_quoted(self):
        r = good_review()
        r["hunks"]["H4"] = {"status": "finding"}
        r["findings"].append({"id": "F2", "hunk": "H4", "line": 1, "quote": "x", "severity": "nit",
                              "claim": "c", "verify": {"verdict": "unsure", "reason": "r"}})
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("binary" in x for x in e))

    def test_same_reviewer_warning(self):
        r = good_review(); r["findings"][0]["verify"]["by"] = "finder"
        _, w = hl.check_review(self.ledger, r)
        self.assertTrue(any("same reviewer" in x for x in w))

    def test_claim_required(self):
        r = good_review(); r["findings"][0]["claim"] = ""
        e, _ = hl.check_review(self.ledger, r)
        self.assertTrue(any("no claim" in x for x in e))

    def test_mostly_skipped_warning(self):
        r = good_review()
        r["hunks"] = {h: {"status": "skipped", "note": "later"} for h in ("H1", "H2", "H3", "H4")}
        r["findings"] = []
        _, w = hl.check_review(self.ledger, r)
        self.assertTrue(any("more than half" in x for x in w))


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.ledger = ledger_of()

    def test_hold_on_confirmed_major(self):
        self.assertEqual(hl.decide(self.ledger, good_review())["verdict"], "HOLD")

    def test_exit_codes(self):
        self.assertEqual(hl.decide(self.ledger, good_review())["exit"], 1)

    def test_refuted_does_not_block(self):
        r = good_review(); r["findings"][0]["verify"]["verdict"] = "refuted"
        self.assertEqual(hl.decide(self.ledger, r)["verdict"], "PASS")

    def test_unsure_blocks_conservatively(self):
        r = good_review(); r["findings"][0]["verify"]["verdict"] = "unsure"
        self.assertEqual(hl.decide(self.ledger, r)["verdict"], "HOLD")

    def test_stop_line_critical_lets_major_pass(self):
        res = hl.decide(self.ledger, good_review(), block="critical")
        self.assertEqual(res["verdict"], "PASS")
        self.assertEqual(len(res["below_line"]), 1)

    def test_stop_line_nit_blocks_minor(self):
        r = good_review(); r["findings"][0]["severity"] = "minor"
        self.assertEqual(hl.decide(self.ledger, r, block="nit")["verdict"], "HOLD")

    def test_incomplete_beats_hold(self):
        r = good_review(); del r["hunks"]["H2"]
        res = hl.decide(self.ledger, r)
        self.assertEqual((res["verdict"], res["exit"]), ("INCOMPLETE", 2))

    def test_stale_hash(self):
        res = hl.decide(self.ledger, good_review(), current_hash="0000")
        self.assertEqual(res["verdict"], "INCOMPLETE")
        self.assertIn("moved", res["errors"][0])

    def test_matching_hash_ok(self):
        res = hl.decide(self.ledger, good_review(), current_hash=self.ledger["diff_hash"])
        self.assertEqual(res["verdict"], "HOLD")

    def test_receipt_text(self):
        r = good_review()
        txt = hl.render_receipt(self.ledger, r, hl.decide(self.ledger, r), "major")
        self.assertIn("verdict: HOLD", txt)
        self.assertIn("F1 [major, confirmed]", txt)
        self.assertIn("Not reviewed:", txt)
        self.assertIn("H4 binary image", txt)

    def test_receipt_lists_refuted(self):
        r = good_review(); r["findings"][0]["verify"]["verdict"] = "refuted"
        txt = hl.render_receipt(self.ledger, r, hl.decide(self.ledger, r), "major")
        self.assertIn("Refuted by the verifier: F1", txt)


class CallerTests(unittest.TestCase):
    def test_symbols_from_header_and_lines(self):
        h = {"header": "def charge(order):", "binary": False,
             "lines": [["+", None, 1, "function applyCoupon(cart) {"], ["+", None, 2, "func (s *Srv) Refund(id string) error {"]]}
        self.assertEqual(hl.symbols_in(h), {"charge", "applyCoupon", "Refund"})

    def test_arrow_and_rust(self):
        h = {"header": "", "binary": False,
             "lines": [["+", None, 1, "export const totalOf = (items) => items.length"], ["-", 3, None, "pub fn settle(x: u8) {"]]}
        self.assertEqual(hl.symbols_in(h), {"totalOf", "settle"})

    def test_keywords_ignored(self):
        h = {"header": "", "binary": False, "lines": [["+", None, 1, "    if (x) {"]]}
        self.assertEqual(hl.symbols_in(h), set())

    def test_find_callers_outside_diff(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            open(os.path.join(d, "src", "pay.py"), "w").write("def charge(o):\n    pass\n")
            open(os.path.join(d, "api.py"), "w").write("from src.pay import charge\ncharge(order)\n")
            os.makedirs(os.path.join(d, "node_modules"))
            open(os.path.join(d, "node_modules", "x.js"), "w").write("charge(1)\n")
            hits = hl.find_callers(ledger_of(), d)
            self.assertEqual(hits["charge"], ["api.py:2"])


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = hl.main(list(argv))
        return code, buf.getvalue()

    def test_split_check_receipt_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            diff = os.path.join(d, "c.diff"); open(diff, "w").write(SAMPLE)
            out = os.path.join(d, "out")
            code, text = self.run_cli("split", "--diff", diff, "--out", out)
            self.assertEqual(code, 0)
            self.assertIn("4 hunks in 3 files", text)
            brief = open(os.path.join(out, "brief.md")).read()
            self.assertIn("## H1  src/pay.py", brief)
            rv = os.path.join(d, "review.json"); json.dump(good_review(), open(rv, "w"))
            code, text = self.run_cli("check", os.path.join(out, "ledger.json"), rv)
            self.assertEqual(code, 0)
            code, text = self.run_cli("receipt", os.path.join(out, "ledger.json"), rv, "--recheck", "--diff", diff)
            self.assertEqual(code, 1)
            self.assertIn("HOLD", text)

    def test_split_empty_diff_exit_3(self):
        with tempfile.TemporaryDirectory() as d:
            diff = os.path.join(d, "e.diff"); open(diff, "w").write("")
            code, _ = self.run_cli("split", "--diff", diff, "--out", os.path.join(d, "o"))
            self.assertEqual(code, 3)

    def test_receipt_json(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "l.json"); json.dump(ledger_of(), open(lp, "w"))
            rv = os.path.join(d, "r.json"); r = good_review(); r["findings"][0]["verify"]["verdict"] = "refuted"
            json.dump(r, open(rv, "w"))
            code, text = self.run_cli("receipt", lp, rv, "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(text)["verdict"], "PASS")

    def test_git_mode(self):
        with tempfile.TemporaryDirectory() as d:
            g = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t"] + list(a), cwd=d, check=True, capture_output=True)
            g("init", "-q"); open(os.path.join(d, "a.py"), "w").write("x = 1\n"); g("add", "."); g("commit", "-qm", "i")
            open(os.path.join(d, "a.py"), "w").write("x = 2\n")
            open(os.path.join(d, "new.py"), "w").write("y = 3\n")
            text, base = hl.collect_git_diff(None, d)
            files = sorted(h["file"] for h in hl.parse_diff(text))
            self.assertEqual(files, ["a.py", "new.py"])
            self.assertEqual(base, "HEAD")


if __name__ == "__main__":
    unittest.main()
