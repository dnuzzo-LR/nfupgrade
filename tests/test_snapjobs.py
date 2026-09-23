import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

from nfupgrader import snapjobs, reports
from nfupgrader.audit import AuditLog
from nfupgrader.host import FakeHost, RunResult

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "reports.test.json")
T0 = datetime.datetime(2026, 9, 23, 14, 0, 0)


def sync(fn):
    fn()


class Hold(object):
    """spawn stand-in that keeps the job function instead of running it."""

    def __init__(self):
        self.fns = []

    def __call__(self, fn):
        self.fns.append(fn)


def ticking(start=T0):
    state = {"t": start}

    def now():
        state["t"] += datetime.timedelta(seconds=1)
        return state["t"]
    return now


def host(events=None):
    h = FakeHost({("sh", "-c", "echo v"): RunResult(0, "v1\n", ""),
                  ("sh", "-c", "df"): RunResult(0, "ok\n", "")})
    if events is not None:
        orig = h.run

        def run(argv, timeout=None):
            events.append("report")
            return orig(argv, timeout)
        h.run = run
    return h


def statuses(seq):
    """read_status stand-in: yields seq, then repeats its last value."""
    it = iter(seq)
    last = {"v": None}

    def read():
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]
    return read


def clock(step=30):
    state = {"t": -step}

    def now():
        state["t"] += step
        return state["t"]
    return now


class RunnerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "nfupgrader_reports")
        self.audit = AuditLog(os.path.join(self.tmp, "audit.jsonl"))

    def runner(self, spawn=sync, h=None, path=FIXTURE, keep=20):
        return snapjobs.SnapshotRunner(h or host(), path, self.root, audit=self.audit,
                                       spawn=spawn, now=ticking(), keep=keep)

    def events(self):
        return [json.loads(l) for l in open(os.path.join(self.tmp, "audit.jsonl"))]


class TestRunner(RunnerBase):
    def test_sync_run_writes_snapshot_and_audits(self):
        r = self.runner()
        sid = r.start("manual", client="1.2.3.4")
        self.assertIsNone(r.job())
        man = reports.read_manifest(self.root, sid)
        self.assertEqual(man["status"], "complete")
        self.assertEqual([x["name"] for x in man["reports"]], ["Version", "Disk Check"])
        ev = [e for e in self.events() if e["event"] == "report_snapshot"]
        self.assertEqual(ev[0]["id"], sid)
        self.assertEqual(ev[0]["client"], "1.2.3.4")

    def test_busy_while_job_held(self):
        hold = Hold()
        r = self.runner(spawn=hold)
        sid = r.start("manual")
        self.assertTrue(r.busy())
        self.assertEqual(r.job()["id"], sid)
        self.assertEqual(r.live_id(), sid)
        with self.assertRaises(snapjobs.Busy):
            r.start("manual")
        hold.fns[0]()
        self.assertFalse(r.busy())

    def test_progress_visible_during_run(self):
        seen = []
        h = host()
        r = self.runner(h=h)
        orig = h.run

        def run(argv, timeout=None):
            seen.append((r.job()["index"], r.job()["current"]))
            return orig(argv, timeout)
        h.run = run
        r.start("manual")
        self.assertEqual(seen, [(1, "Version"), (2, "Disk Check")])

    def test_then_runs_after_job_cleared(self):
        r = self.runner()
        seen = []
        sid = r.start("before", then=lambda i: seen.append((i, r.busy())))
        self.assertEqual(seen, [(sid, False)])

    def test_snapshot_error_audited_and_then_still_runs(self):
        r = self.runner()
        seen = []
        with mock.patch("nfupgrader.snapjobs.reports.take_snapshot",
                        side_effect=OSError("disk full")):
            r.start("before", then=seen.append)
        self.assertEqual(len(seen), 1)
        self.assertIn("report_snapshot_error", [e["event"] for e in self.events()])
        self.assertFalse(r.busy())

    def test_missing_report_file_gives_empty_snapshot(self):
        r = self.runner(path=os.path.join(self.tmp, "nope.json"))
        sid = r.start("manual")
        self.assertEqual(reports.read_manifest(self.root, sid)["reports"], [])

    def test_prunes_after_run(self):
        r = self.runner(keep=1)
        r.start("manual")
        second = r.start("manual")
        self.assertEqual(os.listdir(self.root), [second])


class TestWatch(unittest.TestCase):
    def run_watch(self, seq, grace=300, start_after=None):
        calls = []
        note = snapjobs.watch_upgrade(statuses(seq), start_after or calls.append,
                                      sleep=lambda s: None, clock=clock(), grace=grace)
        return note, calls

    def test_done_after_running(self):
        note, calls = self.run_watch(["not_started", "running", "running", "done"])
        self.assertEqual(calls, [""])

    def test_died_after_running(self):
        note, calls = self.run_watch(["running", "died"])
        self.assertEqual(calls, ["upgrade died before completion"])

    def test_stale_done_ignored_until_running_seen(self):
        note, calls = self.run_watch(["done", "running", "done"])
        self.assertEqual(calls, [""])

    def test_never_running_hits_grace(self):
        note, calls = self.run_watch(["not_started"], grace=300)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("upgrade not observed running"))
        self.assertIn("not_started", calls[0])

    def test_retries_while_busy(self):
        attempts = []

        def start_after(note):
            attempts.append(note)
            if len(attempts) == 1:
                raise snapjobs.Busy()
        self.run_watch(["running", "done"], start_after=start_after)
        self.assertEqual(attempts, ["", ""])


class TestBeginUpgrade(RunnerBase):
    def test_before_then_launch_then_after(self):
        events = []
        r = self.runner(h=host(events))
        before = snapjobs.begin_upgrade(
            r, lambda: events.append("launch"), statuses(["running", "done"]),
            client="ip", audit=self.audit, spawn=sync, sleep=lambda s: None, clock=clock())
        self.assertEqual(events, ["report", "report", "launch", "report", "report"])
        snaps = reports.list_snapshots(self.root)
        self.assertEqual([s["label"] for s in snaps], ["after", "before"])
        self.assertEqual(snaps[0]["pair_of"], before)
        self.assertEqual(snaps[1]["id"], before)

    def test_launch_failure_audited_no_after(self):
        r = self.runner()

        def boom():
            raise OSError("no nf_upgrade")
        snapjobs.begin_upgrade(r, boom, statuses(["running", "done"]), client="ip",
                               audit=self.audit, spawn=sync, sleep=lambda s: None, clock=clock())
        self.assertEqual([s["label"] for s in reports.list_snapshots(self.root)], ["before"])
        err = [e for e in self.events() if e["event"] == "launch_error"]
        self.assertEqual(err[0]["error"], "no nf_upgrade")

    def test_busy_propagates(self):
        r = self.runner(spawn=Hold())
        r.start("manual")
        with self.assertRaises(snapjobs.Busy):
            snapjobs.begin_upgrade(r, lambda: None, statuses(["done"]), client="ip")


if __name__ == "__main__":
    unittest.main()
