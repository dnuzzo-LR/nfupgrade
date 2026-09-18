import os
import tempfile
import unittest
from nfupgrader import logtail


class TestParse(unittest.TestCase):
    def test_parse_well_formed(self):
        e = logtail.parse_line("2026-09-18 10:13:33 INFO  Exporting GSHM data")
        self.assertEqual(e["severity"], "INFO")
        self.assertEqual(e["msg"], "Exporting GSHM data")
        self.assertEqual(e["ts"], "2026-09-18 10:13:33")

    def test_parse_exec(self):
        e = logtail.parse_line("2026-09-18 10:13:33 EXEC  [rpm] netFLEX-CORE-5.4.0-29")
        self.assertEqual(e["severity"], "EXEC")
        self.assertEqual(e["msg"], "[rpm] netFLEX-CORE-5.4.0-29")

    def test_unmatched_line_is_info_raw(self):
        e = logtail.parse_line("legacy child output with no prefix")
        self.assertEqual(e["severity"], "INFO")
        self.assertEqual(e["msg"], "legacy child output with no prefix")
        self.assertIsNone(e["ts"])


class TestTail(unittest.TestCase):
    def _write(self):
        fd, path = tempfile.mkstemp()
        os.write(fd, b"2026-09-18 10:00:00 INFO  a\n"
                     b"2026-09-18 10:00:01 WARN  b\n"
                     b"2026-09-18 10:00:02 ERROR c\n"
                     b"2026-09-18 10:00:03 EXEC  [x] d\n")
        os.close(fd)
        return path

    def test_tail_all(self):
        entries = logtail.tail(self._write())
        self.assertEqual(len(entries), 4)

    def test_min_severity_filter(self):
        entries = logtail.tail(self._write(), min_severity="WARN")
        sevs = [e["severity"] for e in entries]
        self.assertEqual(sevs, ["WARN", "ERROR"])  # EXEC and INFO excluded

    def test_tail_since_offset_returns_only_new(self):
        path = self._write()
        chunk = logtail.tail_since(path, 0)
        self.assertEqual(len(chunk["entries"]), 4)
        self.assertGreater(chunk["next_offset"], 0)
        again = logtail.tail_since(path, chunk["next_offset"])
        self.assertEqual(len(again["entries"]), 0)

    def test_tail_since_truncation_restarts(self):
        path = self._write()
        chunk = logtail.tail_since(path, 999999)  # offset past EOF -> restart
        self.assertEqual(len(chunk["entries"]), 4)


if __name__ == "__main__":
    unittest.main()
