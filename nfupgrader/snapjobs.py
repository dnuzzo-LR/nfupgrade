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
UP_WAIT = 45 * 60  # seconds to wait for netFLEX to come up before the after snapshot


class Busy(Exception):
    """A snapshot job is already running."""


def thread_spawn(fn):
    """Run fn on a daemon thread."""
    t = threading.Thread(target=fn)
    t.daemon = True
    t.start()


def _safe_record(audit, event, **fields):
    """Call audit.record(event, **fields) if audit is set, swallowing any
    exception. A broken audit sink must never change control flow."""
    if audit is None:
        return
    try:
        audit.record(event, **fields)
    except Exception:
        pass


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
        self._waiting = None

    def set_waiting(self, text):
        """What an after snapshot is waiting for (None when not waiting); shown
        by /api/reports/job between the upgrade's end and the snapshot."""
        with self._lock:
            self._waiting = text

    def waiting(self):
        with self._lock:
            return self._waiting

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
        except (IOError, OSError, ValueError) as e:
            self._audit("reports_config_error", path=self.reports_path, error=str(e))
            return []

    def _audit(self, event, **fields):
        """Record an audit event, swallowing any exception from the sink."""
        _safe_record(self.audit, event, **fields)

    def start(self, label, mode=None, pair_of=None, note="", then=None, client="auto"):
        """Start a snapshot; returns its id. Raises Busy if one is running.
        `then(snap_id)` runs once the snapshot itself is finished (whether or not
        it succeeded), while the runner is still marked busy -- busy() only
        clears after `then` returns. `then` must not call start() synchronously
        (it will raise Busy); spawn any follow-up job instead."""
        report_list = self._load()
        with self._lock:
            if self._job is not None:
                raise Busy()
            if not os.path.isdir(self.root):
                os.makedirs(self.root)
            snap_id = reports.new_snapshot_id(self.root, label, self.now())
            self._job = {"id": snap_id, "label": label, "index": 0,
                         "total": len(report_list), "current": "", "status": "running"}
        self._audit("report_snapshot", id=snap_id, label=label, client=client)

        def work():
            try:
                try:
                    reports.take_snapshot(self.host, report_list, self.root, snap_id, label,
                                          mode=mode, pair_of=pair_of, note=note,
                                          on_progress=self._progress, now=self.now)
                    # An after's before must survive its own after's prune.
                    reports.prune(self.root, self.keep,
                                  protect=(snap_id,) + ((pair_of,) if pair_of else ()))
                except Exception as e:
                    self._audit("report_snapshot_error", id=snap_id, error=str(e))
                if then is not None:
                    try:
                        then(snap_id)
                    except Exception as e:
                        self._audit("report_then_error", id=snap_id, error=str(e))
            finally:
                # Stays busy through `then` so callers (like /api/launch) can't
                # race a launch it triggers; only cleared once `then` returns.
                with self._lock:
                    self._job = None

        self.spawn(work)
        return snap_id


def watch_upgrade(read_status, start_after, sleep=time.sleep, clock=time.time,
                  grace=GRACE, interval=INTERVAL, max_read_errors=10,
                  app_up=None, up_wait=UP_WAIT, on_wait=None):
    """Poll read_status() until the upgrade ends, then start_after(note), retrying
    while a snapshot is busy. done/died only count once 'running' was seen, since
    the state file can still hold a terminal state from an earlier run.

    read_status() may raise (transient read of a state file mid-write, a peer
    briefly unreachable, etc); that's treated as status "unreadable" for the
    iteration rather than aborting the watch. If 'running' was already seen and
    reads fail `max_read_errors` times in a row, the watch gives up rather than
    polling forever; a successful read resets the count.

    With app_up, a finished upgrade waits (up to up_wait) for netFLEX to be up
    before the after snapshot -- the engine's boot, or a manual one from the
    Boot menu; on_wait(text) reports the wait and on_wait(None) its end. A
    failed or died upgrade is not waited for. (#7)"""
    t0 = clock()
    seen_running = False
    read_errors = 0
    while True:
        try:
            st = read_status()
        except Exception:
            st = "unreadable"
            read_errors += 1
        else:
            read_errors = 0
        if st == "running":
            seen_running = True
        if seen_running and st == "done":
            note = ""
            break
        elif seen_running and st == "died":
            note = "upgrade died before completion"
            break
        elif seen_running and st == "failed":
            note = "upgrade failed before completion"
            break
        elif seen_running and read_errors >= max_read_errors:
            note = "progress unreadable after upgrade started"
            break
        elif not seen_running and clock() - t0 >= grace:
            note = "upgrade not observed running (status: {})".format(st)
            break
        sleep(interval)
    if note == "" and app_up is not None:
        note = _wait_for_app(app_up, sleep, clock, up_wait, interval, on_wait)
    while True:
        try:
            start_after(note)
            return note
        except Busy:
            sleep(interval)


def _wait_for_app(app_up, sleep, clock, up_wait, interval, on_wait):
    """Poll app_up() until true or up_wait passes; returns the after note."""
    def report(text):
        if on_wait is not None:
            try:
                on_wait(text)
            except Exception:
                pass
    t1 = clock()
    note = ""
    while True:
        try:
            up = app_up()
        except Exception:
            up = False
        if up:
            break
        if clock() - t1 >= up_wait:
            note = "netFLEX was not up {} min after the upgrade finished".format(up_wait // 60)
            break
        report("After snapshot: waiting for netFLEX to come up (up to {} min)".format(up_wait // 60))
        sleep(interval)
    report(None)
    return note


def follow_upgrade(runner, read_status, before_id, app_up=None, spawn=thread_spawn,
                   sleep=time.sleep, clock=time.time):
    """Watch an upgrade already launched and take its after snapshot, paired
    with before_id (the original upgrade's before; None if unknown). Used by
    begin_upgrade and by a continued or resumed upgrade, which take no before."""
    def start_after(note):
        runner.start("after", mode="upgrade", pair_of=before_id, note=note)
    spawn(lambda: watch_upgrade(read_status, start_after, sleep, clock, app_up=app_up,
                                on_wait=runner.set_waiting))


def begin_upgrade(runner, launch, read_status, client, audit=None, spawn=thread_spawn,
                  sleep=time.sleep, clock=time.time, on_error=None, app_up=None,
                  on_before=None):
    """Take the before snapshot, then launch(), then watch for completion and take
    the after snapshot. Returns the before id; raises Busy if a job is running.
    on_before(before_id) runs before launch() (httpd records the id for a later
    continue/Resume, whose after pairs with it).
    A launch() failure is audited and ends the sequence (no after snapshot). If
    `on_error` is given, it is called with the error string so the caller (e.g.
    httpd) can surface the failure elsewhere; on_error is guarded — it must
    never raise out of the background job."""
    def then(before_id):
        if on_before is not None:
            try:
                on_before(before_id)
            except Exception as e:
                _safe_record(audit, "report_then_error", id=before_id, error=str(e))
        try:
            launch()
        except Exception as e:
            _safe_record(audit, "launch_error", error=str(e), client=client)
            if on_error is not None:
                try:
                    on_error(str(e))
                except Exception:
                    pass
            return

        follow_upgrade(runner, read_status, before_id, app_up=app_up, spawn=spawn,
                       sleep=sleep, clock=clock)

    return runner.start("before", mode="upgrade", then=then, client=client)
