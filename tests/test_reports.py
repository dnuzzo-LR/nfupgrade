import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

from nfupgrader import reports
from nfupgrader.host import FakeHost, RunResult

REPS = [{"name": "Version", "command": "echo v"}, {"name": "Disk Check", "command": "df"}]
T0 = datetime.datetime(2026, 9, 23, 14, 2, 0)


def fake_host(version="v1\n", disk=None):
    return FakeHost({
        ("sh", "-c", "echo v"): RunResult(0, version, ""),
        ("sh", "-c", "df"): disk or RunResult(0, "/usr4 10%\n", ""),
    })


class RecordingHost(object):
    """Records (argv, timeout); raises for commands listed in `boom`."""

    def __init__(self, boom=()):
        self.calls = []
        self.boom = boom

    def run(self, argv, timeout=None):
        self.calls.append((list(argv), timeout))
        if argv[-1] in self.boom:
            raise OSError("exec failed")
        return RunResult(0, "ok\n", "")


class SnapshotBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        reports._clear_cache()

    def snap(self, host, label="manual", when=T0, report_list=REPS, **kw):
        sid = reports.new_snapshot_id(self.root, label, when)
        reports.take_snapshot(host, report_list, self.root, sid, label,
                              now=lambda: when, **kw)
        return sid

    def manifest(self, sid):
        return json.load(open(os.path.join(self.root, sid, "manifest.json")))


class TestDefinitions(unittest.TestCase):
    def test_load_skips_function_entries(self):
        path = os.path.join(tempfile.mkdtemp(), "r.json")
        json.dump([{"name": "M", "command": "manage_reports", "type": "function"},
                   {"name": "A", "command": "true", "type": "shell"},
                   {"name": "B", "command": "false"}], open(path, "w"))
        self.assertEqual(reports.load_reports(path),
                         [{"name": "A", "command": "true"}, {"name": "B", "command": "false"}])

    def test_default_file_has_21_reports(self):
        path = os.path.join(os.path.dirname(reports.__file__), "reports.default.json")
        reps = reports.load_reports(path)
        self.assertEqual(len(reps), 21)
        self.assertIn("INC Info", [r["name"] for r in reps])

    def test_safe_name(self):
        self.assertEqual(reports.safe_name("Disk File Check /usr4!"), "Disk_File_Check_usr4")
        self.assertEqual(reports.safe_name("///"), "report")


class TestSnapshot(SnapshotBase):
    def test_new_id_rejects_bad_label(self):
        with self.assertRaises(ValueError):
            reports.new_snapshot_id(self.root, "bogus", T0)

    def test_new_id_avoids_collision(self):
        first = reports.new_snapshot_id(self.root, "before", T0)
        self.assertEqual(first, "20260923_140200_before")
        os.makedirs(os.path.join(self.root, first))
        self.assertEqual(reports.new_snapshot_id(self.root, "before", T0),
                         "20260923_140200_before_2")

    def test_files_and_manifest(self):
        sid = self.snap(fake_host(), label="before", mode="upgrade")
        d = os.path.join(self.root, sid)
        self.assertEqual(open(os.path.join(d, "01_Version.txt")).read(), "v1\n")
        self.assertEqual(open(os.path.join(d, "02_Disk_Check.txt")).read(), "/usr4 10%\n")
        m = self.manifest(sid)
        self.assertEqual(m["status"], "complete")
        self.assertEqual(m["label"], "before")
        self.assertEqual(m["mode"], "upgrade")
        self.assertEqual(m["started"], "2026-09-23T14:02:00")
        self.assertEqual(m["finished"], "2026-09-23T14:02:00")
        self.assertEqual([r["rc"] for r in m["reports"]], [0, 0])
        self.assertEqual([r["error"] for r in m["reports"]], [None, None])
        self.assertEqual(m["reports"][1]["command"], "df")

    def test_stderr_appended_after_marker(self):
        sid = self.snap(fake_host(disk=RunResult(2, "partial", "boom\n")))
        text = open(os.path.join(self.root, sid, "02_Disk_Check.txt")).read()
        self.assertEqual(text, "partial\n----- STDERR -----\nboom\n")
        self.assertEqual(self.manifest(sid)["reports"][1]["rc"], 2)

    def test_timeout_recorded(self):
        sid = self.snap(fake_host(disk=RunResult(124, "", "timeout: sh -c df")))
        entry = self.manifest(sid)["reports"][1]
        self.assertEqual(entry["error"], "timeout")
        self.assertIsNone(entry["rc"])

    def test_exception_recorded_and_timeout_passed(self):
        host = RecordingHost(boom=("df",))
        sid = self.snap(host)
        entry = self.manifest(sid)["reports"][1]
        self.assertEqual(entry["error"], "exec failed")
        self.assertIsNone(entry["rc"])
        self.assertEqual(host.calls[0], (["sh", "-c", "echo v"], reports.REPORT_TIMEOUT))

    def test_progress_callback(self):
        seen = []
        self.snap(fake_host(), on_progress=lambda i, n, name: seen.append((i, n, name)))
        self.assertEqual(seen, [(1, 2, "Version"), (2, 2, "Disk Check")])

    def test_progress_callback_error_does_not_abort_snapshot(self):
        def boom(i, n, name):
            raise RuntimeError("ui gone")
        sid = self.snap(fake_host(), on_progress=boom)
        m = self.manifest(sid)
        self.assertEqual(m["status"], "complete")
        self.assertEqual([r["name"] for r in m["reports"]], ["Version", "Disk Check"])

    def test_round_trips_non_utf8_and_unicode_output(self):
        host = FakeHost({
            ("sh", "-c", "echo v"): RunResult(0, "v1\n", ""),
            ("sh", "-c", "df"): RunResult(0, "bad:� ok:é\n", ""),
        })
        sid = self.snap(host)
        self.assertEqual(reports.report_text(self.root, sid, "Disk Check"),
                         "bad:� ok:é\n")

    def test_pair_and_note_stored(self):
        sid = self.snap(fake_host(), label="after", pair_of="20260923_140000_before", note="x")
        m = self.manifest(sid)
        self.assertEqual(m["pair_of"], "20260923_140000_before")
        self.assertEqual(m["note"], "x")


class TestListing(SnapshotBase):
    def test_newest_first_and_incomplete(self):
        old = self.snap(fake_host(), when=T0)
        new = self.snap(fake_host(), when=T0 + datetime.timedelta(minutes=1))
        path = os.path.join(self.root, old, "manifest.json")
        m = json.load(open(path))
        m["status"] = "running"
        json.dump(m, open(path, "w"))
        listed = reports.list_snapshots(self.root)
        self.assertEqual([s["id"] for s in listed], [new, old])
        self.assertEqual(listed[1]["status"], "incomplete")
        self.assertNotIn("command", listed[0]["reports"][0])
        self.assertEqual(reports.list_snapshots(self.root, live_id=old)[1]["status"], "running")

    def test_ignores_junk_and_missing_root(self):
        self.snap(fake_host())
        os.makedirs(os.path.join(self.root, "junk"))
        self.assertEqual(len(reports.list_snapshots(self.root)), 1)
        self.assertEqual(reports.list_snapshots(os.path.join(self.root, "nope")), [])

    def test_prune_keeps_newest(self):
        ids = [self.snap(fake_host(), when=T0 + datetime.timedelta(seconds=i)) for i in range(5)]
        reports.prune(self.root, keep=3)
        self.assertEqual(sorted(os.listdir(self.root)), ids[2:])

    def test_prune_never_removes_protected(self):
        ids = [self.snap(fake_host(), when=T0 + datetime.timedelta(seconds=i)) for i in range(5)]
        reports.prune(self.root, keep=2, protect=(ids[0],))
        self.assertEqual(sorted(os.listdir(self.root)), [ids[0], ids[4]])

    def test_prune_deletes_pairs_together(self):
        t = lambda i: T0 + datetime.timedelta(seconds=i)
        before = self.snap(fake_host(), label="before", when=t(0))
        after = self.snap(fake_host(), label="after", when=t(1), pair_of=before)
        m2 = self.snap(fake_host(), when=t(2))
        m3 = self.snap(fake_host(), when=t(3))
        # one over the limit: the oldest unit is the before/after pair, both go
        reports.prune(self.root, keep=3)
        self.assertEqual(sorted(os.listdir(self.root)), [m2, m3])

    def test_prune_never_splits_pair_by_removing_after_alone(self):
        t = lambda i: T0 + datetime.timedelta(seconds=i)
        m0 = self.snap(fake_host(), when=t(0))
        before = self.snap(fake_host(), label="before", when=t(1))
        after = self.snap(fake_host(), label="after", when=t(2), pair_of=before)
        m3 = self.snap(fake_host(), when=t(3))
        reports.prune(self.root, keep=3, protect=(after,))
        # the pair is protected as a unit via its after; only m0 goes
        self.assertEqual(sorted(os.listdir(self.root)), [before, after, m3])

    def test_prune_keeps_newest_unpaired_before(self):
        t = lambda i: T0 + datetime.timedelta(seconds=i)
        old_before = self.snap(fake_host(), label="before", when=t(0))
        before = self.snap(fake_host(), label="before", when=t(1))
        ms = [self.snap(fake_host(), when=t(2 + i)) for i in range(3)]
        # an upgrade is in flight from `before` (no after yet): keep it even
        # though it is among the oldest; the older unpaired before is fair game
        reports.prune(self.root, keep=2)
        self.assertEqual(sorted(os.listdir(self.root)), [before, ms[2]])

    def test_prune_unpaired_before_not_protected_once_newer_before_paired(self):
        t = lambda i: T0 + datetime.timedelta(seconds=i)
        b0 = self.snap(fake_host(), label="before", when=t(0))
        b1 = self.snap(fake_host(), label="before", when=t(1))
        a1 = self.snap(fake_host(), label="after", when=t(2), pair_of=b1)
        reports.prune(self.root, keep=2)
        self.assertEqual(sorted(os.listdir(self.root)), [b1, a1])

    def test_read_manifest_rejects_bad_ids(self):
        self.assertIsNone(reports.read_manifest(self.root, "../etc"))
        self.assertIsNone(reports.read_manifest(self.root, "x"))
        self.assertIsNone(reports.read_manifest(self.root, "20260923_140200_manual"))

    def test_report_text(self):
        sid = self.snap(fake_host())
        self.assertEqual(reports.report_text(self.root, sid, "Version"), "v1\n")
        self.assertIsNone(reports.report_text(self.root, sid, "Nope"))
        self.assertIsNone(reports.report_text(self.root, "../x", "Version"))


class TestCompare(SnapshotBase):
    def test_classification(self):
        a = self.snap(fake_host(), when=T0)
        b = self.snap(fake_host(version="v2\n", disk=RunResult(1, "/usr4 10%\n", "")),
                      when=T0 + datetime.timedelta(minutes=1),
                      report_list=REPS + [{"name": "Extra", "command": "true"}])
        res = reports.compare(self.root, a, b)
        self.assertEqual((res["a"], res["b"]), (a, b))
        rows = dict((r["name"], r) for r in res["reports"])
        self.assertEqual([r["name"] for r in res["reports"]], ["Version", "Disk Check", "Extra"])
        self.assertEqual(rows["Version"]["status"], "changed")
        self.assertEqual((rows["Version"]["added"], rows["Version"]["removed"]), (1, 1))
        self.assertEqual(rows["Disk Check"]["status"], "failed")
        self.assertEqual(rows["Extra"]["status"], "missing")
        self.assertEqual(rows["Extra"]["side"], "b")

    def test_unchanged(self):
        a = self.snap(fake_host(), when=T0)
        b = self.snap(fake_host(), when=T0 + datetime.timedelta(minutes=1))
        statuses = [r["status"] for r in reports.compare(self.root, a, b)["reports"]]
        self.assertEqual(statuses, ["unchanged", "unchanged"])

    def test_unknown_snapshot(self):
        a = self.snap(fake_host())
        self.assertIsNone(reports.compare(self.root, a, "20990101_000000_after"))

    def test_complete_snapshots_are_cached(self):
        a = self.snap(fake_host(), when=T0)
        b = self.snap(fake_host(version="v2\n"), when=T0 + datetime.timedelta(minutes=1))
        with mock.patch.object(reports, "_counts", wraps=reports._counts) as spy:
            first = reports.compare(self.root, a, b)
            second = reports.compare(self.root, a, b)
        self.assertEqual(spy.call_count, len(REPS))
        self.assertIs(first, second)

    def test_running_snapshot_is_not_cached(self):
        a = self.snap(fake_host(), when=T0)
        b = self.snap(fake_host(version="v2\n"), when=T0 + datetime.timedelta(minutes=1))
        path = os.path.join(self.root, b, "manifest.json")
        m = json.load(open(path))
        m["status"] = "running"
        json.dump(m, open(path, "w"))
        with mock.patch.object(reports, "_counts", wraps=reports._counts) as spy:
            reports.compare(self.root, a, b)
            reports.compare(self.root, a, b)
        self.assertEqual(spy.call_count, 2 * len(REPS))


class TestDiffRows(unittest.TestCase):
    def test_unified(self):
        rows = reports.unified_rows(["x", "y", "z"], ["x", "Y", "z"])
        self.assertEqual([r["op"] for r in rows], ["@", " ", "-", "+", " "])
        self.assertEqual(rows[2]["text"], "y")
        self.assertEqual(rows[3]["text"], "Y")

    def test_unified_keeps_content_that_looks_like_headers(self):
        rows = reports.unified_rows(["--a", "keep"], ["keep"])
        self.assertIn({"op": "-", "text": "--a"}, rows)

    def test_unified_identical_is_empty(self):
        self.assertEqual(reports.unified_rows(["a"], ["a"]), [])

    def test_side_collapses_equal_runs(self):
        a = [str(i) for i in range(20)]
        b = list(a)
        b[10] = "TEN"
        rows = reports.side_rows(a, b)
        self.assertEqual([r["op"] for r in rows],
                         ["skip", "equal", "equal", "equal", "replace",
                          "equal", "equal", "equal", "skip"])
        self.assertEqual(rows[0]["count"], 7)
        self.assertEqual(rows[-1]["count"], 6)
        self.assertEqual((rows[4]["left"], rows[4]["right"]), ("10", "TEN"))
        self.assertEqual((rows[1]["left"], rows[1]["right"]), ("7", "7"))

    def test_side_uneven_replace_pads_with_none(self):
        rows = reports.side_rows(["a", "b"], ["c"])
        self.assertEqual([(r["left"], r["right"]) for r in rows], [("a", "c"), ("b", None)])

    def test_side_identical_is_single_skip(self):
        self.assertEqual(reports.side_rows(["a", "b"], ["a", "b"]),
                         [{"op": "skip", "count": 2}])


class TestDiff(SnapshotBase):
    def test_diff_styles_and_cap(self):
        a = self.snap(fake_host(version="1\n2\n3\n"), when=T0)
        b = self.snap(fake_host(version="1\nX\nY\n"), when=T0 + datetime.timedelta(minutes=1))
        side = reports.diff(self.root, a, b, "Version", "side")
        self.assertFalse(side["truncated"])
        self.assertEqual([r["op"] for r in side["rows"]], ["equal", "replace", "replace"])
        uni = reports.diff(self.root, a, b, "Version", "unified")
        self.assertIn({"op": "+", "text": "X"}, uni["rows"])
        capped = reports.diff(self.root, a, b, "Version", "side", cap=2)
        self.assertTrue(capped["truncated"])
        self.assertEqual(len(capped["rows"]), 2)

    def test_diff_one_sided_report(self):
        a = self.snap(fake_host(), when=T0)
        b = self.snap(fake_host(), when=T0 + datetime.timedelta(minutes=1),
                      report_list=REPS + [{"name": "Extra", "command": "true"}])
        # FakeHost answers the unmocked "true" with rc 1 and stderr "unmocked", so b's
        # Extra text is "----- STDERR -----\nunmocked"; a has no Extra -> two inserts.
        res = reports.diff(self.root, a, b, "Extra", "side")
        self.assertEqual([r["op"] for r in res["rows"]], ["insert", "insert"])

    def test_diff_unknown_report(self):
        a = self.snap(fake_host())
        self.assertIsNone(reports.diff(self.root, a, a, "Nope", "side"))

    def test_diff_unknown_snapshot(self):
        a = self.snap(fake_host())
        self.assertIsNone(reports.diff(self.root, a, "20990101_000000_after", "Version", "side"))

    def test_diff_is_cached_for_complete_snapshots(self):
        a = self.snap(fake_host(version="1\n2\n3\n"), when=T0)
        b = self.snap(fake_host(version="1\nX\nY\n"),
                      when=T0 + datetime.timedelta(minutes=1))
        first = reports.diff(self.root, a, b, "Version", "side")
        second = reports.diff(self.root, a, b, "Version", "side")
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
