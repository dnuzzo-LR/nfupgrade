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



class TestLatestTrace(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def touch(self, name, mtime):
        p = os.path.join(self.d, name)
        open(p, "w").write("x\n")
        os.utime(p, (mtime, mtime))
        return p

    def test_newest_pid_file_for_phase_and_host(self):
        self.touch("stage_inc_h1.lab.local.100", 1000)
        newest = self.touch("stage_inc_h1.lab.local.200", 2000)
        self.touch("stage_inc_h1.lab.local.200.web", 3000)
        self.touch("stage_inc_h1.lab.local.200.rpm", 3000)
        self.touch("stage_inc_h1XlabXlocal.300", 4000)   # dots in host are literal
        up = self.touch("upgrade_inc_h1.lab.local.400", 5000)
        self.assertEqual(logtail.latest_trace(self.d, "stage", "h1.lab.local"), newest)
        self.assertEqual(logtail.latest_trace(self.d, "upgrade", "h1.lab.local"), up)

    def test_falls_back_to_pidless_path(self):
        self.assertEqual(logtail.latest_trace(self.d, "stage", "h1"),
                         os.path.join(self.d, "stage_inc_h1"))
        self.assertEqual(logtail.latest_trace(os.path.join(self.d, "nope"), "stage", "h1"),
                         os.path.join(self.d, "nope", "stage_inc_h1"))


class TestLatestOf(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def touch(self, name, mtime):
        p = os.path.join(self.d, name)
        open(p, "w").write("x\n")
        os.utime(p, (mtime, mtime))
        return p

    def test_latest_of_picks_newest_across_phases(self):
        self.touch("install_inc_h1.100", 1000)
        newest = self.touch("reset_inc_h1.200", 2000)
        self.assertEqual(logtail.latest_of(self.d, ["install", "reset"], "h1"), newest)
        # order of phases passed in doesn't matter -- mtime decides
        self.assertEqual(logtail.latest_of(self.d, ["reset", "install"], "h1"), newest)

    def test_falls_back_to_first_phase_pidless(self):
        self.assertEqual(logtail.latest_of(self.d, ["install", "reset"], "h1"),
                         os.path.join(self.d, "install_inc_h1"))


if __name__ == "__main__":
    unittest.main()


class TestLiveStep(unittest.TestCase):
    """run_logged buffers a step's output in ${INCLOGDIR}/.nfrun.<pid> until the
    step ends; live_step shows that buffer while the step runs."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def put(self, name, text, mtime):
        p = os.path.join(self.d, name)
        open(p, "w").write(text)
        os.utime(p, (mtime, mtime))
        return p

    def test_none(self):
        self.assertEqual(logtail.live_step(self.d), (None, []))
        self.assertEqual(logtail.live_step("/nonexistent"), (None, []))

    def test_newest_and_tail(self):
        self.put(".nfrun.100", "old\n", 1000)
        body = "".join("line {}\n".format(i) for i in range(100))
        p = self.put(".nfrun.200", body, 2000)
        self.put("install_inc_h1.200", "not a buffer\n", 3000)
        path, lines = logtail.live_step(self.d, max_lines=5)
        self.assertEqual(path, p)
        self.assertEqual(lines, ["line 95", "line 96", "line 97", "line 98", "line 99"])
