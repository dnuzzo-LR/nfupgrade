import json
import os
import tempfile
import unittest
from nfupgrader.audit import AuditLog, last_unfinished_fix


class TestAudit(unittest.TestCase):
    def test_record_appends_json_lines(self):
        path = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
        a = AuditLog(path)
        a.record("launch", mode="stage", client="127.0.0.1")
        a.record("override", check="disk", reason="known-good")
        lines = open(path).read().strip().splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["event"], "launch")
        self.assertEqual(first["mode"], "stage")
        self.assertIn("ts", first)

    def test_record_is_appendonly_across_instances(self):
        path = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
        AuditLog(path).record("a", n=1)
        AuditLog(path).record("b", n=2)
        self.assertEqual(len(open(path).read().strip().splitlines()), 2)


class TestLastUnfinishedFix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "audit.jsonl")

    def _write(self, lines):
        with open(self.path, "w") as fh:
            for line in lines:
                fh.write(line + "\n")

    def test_missing_file_returns_none(self):
        self.assertIsNone(last_unfinished_fix(os.path.join(self.tmp, "nope.jsonl")))

    def test_no_fix_events_returns_none(self):
        self._write([json.dumps({"ts": "t0", "event": "launch", "mode": "stage"})])
        self.assertIsNone(last_unfinished_fix(self.path))

    def test_finished_fix_returns_none(self):
        self._write([
            json.dumps({"ts": "t1", "event": "check_fix_start", "name": "pkg-ksh", "kind": "rpm"}),
            json.dumps({"ts": "t2", "event": "check_fix", "name": "pkg-ksh", "kind": "rpm", "ok": True}),
        ])
        self.assertIsNone(last_unfinished_fix(self.path))

    def test_unfinished_fix_reported(self):
        self._write([
            json.dumps({"ts": "t1", "event": "check_fix_start", "name": "pkg-ksh", "kind": "rpm"}),
        ])
        self.assertEqual(last_unfinished_fix(self.path),
                         {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"})

    def test_finish_for_different_name_does_not_clear(self):
        self._write([
            json.dumps({"ts": "t1", "event": "check_fix_start", "name": "pkg-ksh", "kind": "rpm"}),
            json.dumps({"ts": "t2", "event": "check_fix", "name": "sdi-mode", "kind": "cfg", "ok": True}),
        ])
        self.assertEqual(last_unfinished_fix(self.path),
                         {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"})

    def test_bad_lines_tolerated(self):
        self._write([
            "not json {{{",
            json.dumps({"ts": "t1", "event": "check_fix_start", "name": "pkg-ksh", "kind": "rpm"}),
            "",
            "also bad",
        ])
        self.assertEqual(last_unfinished_fix(self.path),
                         {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"})

    def test_only_the_last_start_matters(self):
        self._write([
            json.dumps({"ts": "t1", "event": "check_fix_start", "name": "a", "kind": "rpm"}),
            json.dumps({"ts": "t2", "event": "check_fix", "name": "a", "kind": "rpm", "ok": True}),
            json.dumps({"ts": "t3", "event": "check_fix_start", "name": "b", "kind": "rpm"}),
        ])
        self.assertEqual(last_unfinished_fix(self.path),
                         {"name": "b", "kind": "rpm", "ts": "t3"})


if __name__ == "__main__":
    unittest.main()
