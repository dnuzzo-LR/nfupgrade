import datetime
import json
import os
import tempfile
import threading
import time
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


class RaisingAudit(object):
    """audit stand-in whose record() always raises."""

    def record(self, event, **fields):
        raise OSError("audit sink down")


class BlockingHost(object):
    """host stand-in that blocks in run() on `event` for the first call only,
    then answers from `responses` like FakeHost."""

    def __init__(self, event, responses):
        self.event = event
        self.responses = responses
        self.name = "blocking"
        self.local = True
        self._first = True

    def run(self, argv, timeout=None):
        if self._first:
            self._first = False
            self.event.wait(5)
        return self.responses.get(tuple(argv), RunResult(1, "", "unmocked"))


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

    def test_then_runs_while_still_busy(self):
        r = self.runner()
        seen = []
        sid = r.start("before", then=lambda i: seen.append((i, r.busy())))
        self.assertEqual(seen, [(sid, True)])
        self.assertFalse(r.busy())

    def test_then_error_audited_and_runner_idle(self):
        r = self.runner()

        def boom(sid):
            raise ValueError("boom")
        sid = r.start("before", then=boom)
        self.assertFalse(r.busy())
        err = [e for e in self.events() if e["event"] == "report_then_error"]
        self.assertEqual(err[0]["id"], sid)
        self.assertEqual(err[0]["error"], "boom")
        # runner must still be usable afterwards
        sid2 = r.start("manual")
        self.assertEqual(reports.read_manifest(self.root, sid2)["status"], "complete")

    def test_audit_failures_never_change_control_flow(self):
        r = self.runner()
        r.audit = RaisingAudit()
        sid = r.start("manual")
        self.assertIsNotNone(sid)
        man = reports.read_manifest(self.root, sid)
        self.assertEqual(man["status"], "complete")
        self.assertFalse(r.busy())
        # a second start still works normally
        sid2 = r.start("manual")
        self.assertNotEqual(sid, sid2)
        self.assertFalse(r.busy())

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
        bad_path = os.path.join(self.tmp, "nope.json")
        r = self.runner(path=bad_path)
        sid = r.start("manual")
        self.assertEqual(reports.read_manifest(self.root, sid)["reports"], [])
        err = [e for e in self.events() if e["event"] == "reports_config_error"]
        self.assertEqual(err[0]["path"], bad_path)
        self.assertIn("error", err[0])

    def test_malformed_report_config_audited(self):
        bad_path = os.path.join(self.tmp, "bad.json")
        with open(bad_path, "w") as fh:
            fh.write("not json")
        r = self.runner(path=bad_path)
        sid = r.start("manual")
        self.assertEqual(reports.read_manifest(self.root, sid)["reports"], [])
        err = [e for e in self.events() if e["event"] == "reports_config_error"]
        self.assertEqual(err[0]["path"], bad_path)

    def test_prunes_after_run(self):
        r = self.runner(keep=1)
        r.start("manual")
        second = r.start("manual")
        self.assertEqual(os.listdir(self.root), [second])

    def test_prune_protects_live_job_and_its_pair(self):
        r = self.runner()
        with mock.patch("nfupgrader.snapjobs.reports.prune") as prune:
            sid = r.start("after", pair_of="20260923_135959_before")
        prune.assert_called_once_with(self.root, 20,
                                      protect=(sid, "20260923_135959_before"))
        with mock.patch("nfupgrader.snapjobs.reports.prune") as prune:
            sid = r.start("manual")
        prune.assert_called_once_with(self.root, 20, protect=(sid,))

    def test_after_snapshot_keeps_its_before_when_pruning(self):
        r = self.runner(keep=2)
        m = r.start("manual")
        before = r.start("before")
        after = r.start("after", pair_of=before)
        self.assertEqual(sorted(os.listdir(self.root)), [before, after])

    def test_real_thread_stays_busy_until_done(self):
        event = threading.Event()
        responses = {("sh", "-c", "echo v"): RunResult(0, "v1\n", ""),
                     ("sh", "-c", "df"): RunResult(0, "ok\n", "")}
        h = BlockingHost(event, responses)
        r = snapjobs.SnapshotRunner(h, FIXTURE, self.root, audit=self.audit,
                                    spawn=snapjobs.thread_spawn, now=ticking())
        try:
            sid = r.start("manual")
            self.assertTrue(r.busy())
            with self.assertRaises(snapjobs.Busy):
                r.start("manual")
        finally:
            event.set()

        deadline = time.time() + 5
        while r.busy() and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(r.busy())
        man = reports.read_manifest(self.root, sid)
        self.assertEqual(man["status"], "complete")
        self.assertEqual([x["name"] for x in man["reports"]], ["Version", "Disk Check"])


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

    def test_status_read_error_recovers(self):
        calls = {"n": 0}
        seq = iter(["running", "done"])

        def read():
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient")
            return next(seq)
        note = snapjobs.watch_upgrade(read, lambda n: None,
                                      sleep=lambda s: None, clock=clock())
        self.assertEqual(note, "")

    def test_status_unreadable_after_running_gives_up(self):
        calls = {"n": 0}

        def read():
            calls["n"] += 1
            if calls["n"] == 1:
                return "running"
            raise OSError("gone")
        note = snapjobs.watch_upgrade(read, lambda n: None, sleep=lambda s: None,
                                      clock=clock(), max_read_errors=3)
        self.assertEqual(note, "progress unreadable after upgrade started")


class TestBeginUpgrade(RunnerBase):
    def test_before_then_launch_then_after(self):
        events = []
        r = self.runner(h=host(events))
        hold = Hold()
        before = snapjobs.begin_upgrade(
            r, lambda: events.append("launch"), statuses(["running", "done"]),
            client="ip", audit=self.audit, spawn=hold, sleep=lambda s: None, clock=clock())
        # begin_upgrade returns once the before snapshot's `then` has launched;
        # the after-watcher was only handed to `hold`, not run yet.
        self.assertEqual(events, ["report", "report", "launch"])
        hold.fns[0]()
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

    def test_on_error_receives_message(self):
        r = self.runner()

        def boom():
            raise OSError("no nf_upgrade")
        seen = []
        snapjobs.begin_upgrade(r, boom, statuses(["running", "done"]), client="ip",
                               audit=self.audit, spawn=sync, sleep=lambda s: None, clock=clock(),
                               on_error=seen.append)
        self.assertEqual(seen, ["no nf_upgrade"])
        # existing launch_error audit still happens alongside on_error
        err = [e for e in self.events() if e["event"] == "launch_error"]
        self.assertEqual(err[0]["error"], "no nf_upgrade")

    def test_on_error_raising_does_not_propagate(self):
        r = self.runner()

        def boom():
            raise OSError("no nf_upgrade")

        def bad_on_error(msg):
            raise ValueError("callback exploded")
        # must not raise out of begin_upgrade/then
        snapjobs.begin_upgrade(r, boom, statuses(["running", "done"]), client="ip",
                               audit=self.audit, spawn=sync, sleep=lambda s: None, clock=clock(),
                               on_error=bad_on_error)
        self.assertEqual([s["label"] for s in reports.list_snapshots(self.root)], ["before"])

    def test_on_error_not_called_on_success(self):
        r = self.runner(h=host([]))
        seen = []
        snapjobs.begin_upgrade(r, lambda: None, statuses(["running", "done"]), client="ip",
                               audit=self.audit, spawn=Hold(), sleep=lambda s: None, clock=clock(),
                               on_error=seen.append)
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
