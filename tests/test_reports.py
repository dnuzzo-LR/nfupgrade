import datetime
import json
import os
import tempfile
import unittest

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

    def test_read_manifest_rejects_bad_ids(self):
        self.assertIsNone(reports.read_manifest(self.root, "../etc"))
        self.assertIsNone(reports.read_manifest(self.root, "x"))
        self.assertIsNone(reports.read_manifest(self.root, "20260923_140200_manual"))

    def test_report_text(self):
        sid = self.snap(fake_host())
        self.assertEqual(reports.report_text(self.root, sid, "Version"), "v1\n")
        self.assertIsNone(reports.report_text(self.root, sid, "Nope"))
        self.assertIsNone(reports.report_text(self.root, "../x", "Version"))


if __name__ == "__main__":
    unittest.main()
