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


class TestCarriageReturns(unittest.TestCase):
    """#8: zstd and similar redraw a progress bar with \\r; splitting on \\r too
    flooded the Log tab with hundreds of lines. Keep what a terminal shows."""

    def test_terminal_lines(self):
        self.assertEqual(logtail.terminal_lines("a\r b\r c\nd\n"), [" c", "d"])
        self.assertEqual(logtail.terminal_lines("x\r\ny"), ["x", "y"])
        self.assertEqual(logtail.terminal_lines(""), [])

    def test_tail_since_collapses_progress(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t")
        bar = "".join("\r{}% done".format(i) for i in range(0, 101))
        with open(p, "w") as fh:
            fh.write("2026-11-11 19:12:00 INFO  backing up\n" + bar + "\n"
                     "2026-11-11 19:15:00 INFO  backup done\n")
        e = logtail.tail_since(p, 0)["entries"]
        self.assertEqual([x["msg"] for x in e], ["backing up", "100% done", "backup done"])


class TestLiveLog(unittest.TestCase):
    """#8: the newest log being written while the main trace is quiet: a
    run_logged buffer, the trace's sub-logs, or /usr/cnc/logs."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.x = tempfile.mkdtemp()
        self.trace = self.put(self.d, "upgrade_inc_h1.77", "trace\n", 1000)

    def put(self, d, name, text, mtime):
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            fh.write(text)
        os.utime(p, (mtime, mtime))
        return p

    def live(self, now=2000):
        return logtail.live_log(self.d, self.trace, extra_dirs=(self.x,), now=now)

    def test_sub_log_newer_than_trace(self):
        p = self.put(self.d, "upgrade_inc_h1.77.copy_cnc", "copying\r50%\r100%\ncmmperm\n", 1900)
        self.assertEqual(self.live(), {"file": p, "lines": ["100%", "cmmperm"], "age": 100})

    def test_extra_dir_newest_wins(self):
        self.put(self.d, "upgrade_inc_h1.77.rpm", "rpm\n", 1500)
        p = self.put(self.x, "rdb_deploy.log", "pg_upgrade\n", 1990)
        self.assertEqual(self.live()["file"], p)

    def test_nothing_newer_than_trace(self):
        self.put(self.d, "upgrade_inc_h1.77.copy_cnc", "old\n", 900)
        self.put(self.x, "old.log", "old\n", 800)
        self.assertEqual(self.live(), {"file": None, "lines": [], "age": None})

    def test_run_logged_buffer_counts(self):
        p = self.put(self.d, ".nfrun.4242", "Installing nodejs\n", 1200)
        self.assertEqual(self.live()["file"], p)

    def test_run_logged_buffer_even_if_older_than_trace(self):
        # the step in progress by definition (the trace is written around it)
        p = self.put(self.d, ".nfrun.4242", "Installing nodejs\n", 500)
        self.assertEqual(self.live()["file"], p)

    def test_other_traces_ignored(self):
        self.put(self.d, "stage_inc_h1.70", "older run\n", 1999)
        self.put(self.d, "upgrade_inc_h1.7", "a different pid\n", 1999)
        self.assertIsNone(self.live()["file"])

    def test_missing_dirs(self):
        self.assertEqual(logtail.live_log("/nonexistent", None, extra_dirs=("/nope",), now=1),
                         {"file": None, "lines": [], "age": None})
