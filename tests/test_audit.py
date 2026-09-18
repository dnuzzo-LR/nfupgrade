import json
import os
import tempfile
import unittest
from nfupgrader.audit import AuditLog


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


if __name__ == "__main__":
    unittest.main()
