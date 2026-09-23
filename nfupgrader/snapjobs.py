"""snapjobs.py — run report snapshots in the background, one at a time, and
sequence them around an Upgrade launch: before snapshot -> launch -> watch the
progress record -> after snapshot."""
import datetime
import os
import threading
import time

from nfupgrader import reports

GRACE = 300       # seconds to wait for nf_upgrader to be seen running
INTERVAL = 30     # seconds between progress polls / busy retries


class Busy(Exception):
    """A snapshot job is already running."""


def thread_spawn(fn):
    """Run fn on a daemon thread."""
    t = threading.Thread(target=fn)
    t.daemon = True
    t.start()


class SnapshotRunner(object):
    """Owns the single snapshot job. `spawn` runs the job body (a thread in
    production, inline in tests)."""

    def __init__(self, host, reports_path, root, audit=None, spawn=thread_spawn,
                 now=datetime.datetime.now, keep=reports.KEEP):
        self.host = host
        self.reports_path = reports_path
        self.root = root
        self.audit = audit
        self.spawn = spawn
        self.now = now
        self.keep = keep
        self._lock = threading.Lock()
        self._job = None

    def busy(self):
        with self._lock:
            return self._job is not None

    def job(self):
        """A copy of the running job's state, or None when idle."""
        with self._lock:
            return dict(self._job) if self._job else None

    def live_id(self):
        j = self.job()
        return j["id"] if j else None

    def _progress(self, idx, total, name):
        with self._lock:
            if self._job is not None:
                self._job.update(index=idx, total=total, current=name)

    def _load(self):
        try:
            return reports.load_reports(self.reports_path)
        except (IOError, OSError, ValueError):
            return []

    def start(self, label, mode=None, pair_of=None, note="", then=None, client="auto"):
        """Start a snapshot; returns its id. Raises Busy if one is running.
        `then(snap_id)` is called once the job is finished and no longer busy,
        even if the snapshot itself failed."""
        report_list = self._load()
        with self._lock:
            if self._job is not None:
                raise Busy()
            if not os.path.isdir(self.root):
                os.makedirs(self.root)
            snap_id = reports.new_snapshot_id(self.root, label, self.now())
            self._job = {"id": snap_id, "label": label, "index": 0,
                         "total": len(report_list), "current": "", "status": "running"}
        if self.audit is not None:
            self.audit.record("report_snapshot", id=snap_id, label=label, client=client)

        def work():
            try:
                reports.take_snapshot(self.host, report_list, self.root, snap_id, label,
                                      mode=mode, pair_of=pair_of, note=note,
                                      on_progress=self._progress, now=self.now)
                reports.prune(self.root, self.keep, protect=(snap_id,))
            except Exception as e:
                if self.audit is not None:
                    self.audit.record("report_snapshot_error", id=snap_id, error=str(e))
            finally:
                with self._lock:
                    self._job = None
            # Cleared before `then` so the after-watcher it starts can run a job.
            if then is not None:
                then(snap_id)

        self.spawn(work)
        return snap_id


def watch_upgrade(read_status, start_after, sleep=time.sleep, clock=time.time,
                  grace=GRACE, interval=INTERVAL):
    """Poll read_status() until the upgrade ends, then start_after(note), retrying
    while a snapshot is busy. done/died only count once 'running' was seen, since
    the state file can still hold a terminal state from an earlier run."""
    t0 = clock()
    seen_running = False
    while True:
        st = read_status()
        if st == "running":
            seen_running = True
        elif seen_running and st == "done":
            note = ""
            break
        elif seen_running and st == "died":
            note = "upgrade died before completion"
            break
        elif not seen_running and clock() - t0 >= grace:
            note = "upgrade not observed running (status: {})".format(st)
            break
        sleep(interval)
    while True:
        try:
            start_after(note)
            return note
        except Busy:
            sleep(interval)


def begin_upgrade(runner, launch, read_status, client, audit=None, spawn=thread_spawn,
                  sleep=time.sleep, clock=time.time):
    """Take the before snapshot, then launch(), then watch for completion and take
    the after snapshot. Returns the before id; raises Busy if a job is running.
    A launch() failure is audited and ends the sequence (no after snapshot)."""
    def then(before_id):
        try:
            launch()
        except Exception as e:
            if audit is not None:
                audit.record("launch_error", error=str(e), client=client)
            return

        def start_after(note):
            runner.start("after", mode="upgrade", pair_of=before_id, note=note)
        spawn(lambda: watch_upgrade(read_status, start_after, sleep, clock))

    return runner.start("before", mode="upgrade", then=then, client=client)
