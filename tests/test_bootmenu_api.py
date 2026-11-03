import json
import os
import signal
import unittest
from unittest import mock

from nfupgrader import api, bootmenu
from tests.test_api import make_ctx, install_ctx


class StubSession(object):
    """Stand-in for BootMenuSession: records calls instead of touching real
    processes."""

    def __init__(self, running=False, state=None, busy=False, own=None):
        self._running = running
        self._state = state or {"state": "none", "pid": None, "started_at": None,
                                 "exit_code": None}
        self._busy = busy
        self._own = own if own is not None else set()
        self.end_calls = 0

    def running(self):
        return self._running

    def state(self):
        return self._state

    def busy(self):
        return self._busy

    def own_pids(self):
        return self._own

    def end(self, reason=None):
        self.end_calls += 1
        self.end_reason = reason
        return True


def _session_fixture(ctx):
    sid = ctx.auth.redeem("TOK", "127.0.0.1")
    return sid


def _get(ctx, sid, path, **q):
    status, _, body = api.dispatch("GET", path, dict((k, [v]) for k, v in q.items()), b"",
                                   {"nfu_sid": sid}, "127.0.0.1", ctx)
    return status, json.loads(body.decode())


def _post(ctx, sid, path, payload=None):
    body = json.dumps(payload or {}).encode()
    status, _, raw = api.dispatch("POST", path, {}, body, {"nfu_sid": sid}, "127.0.0.1", ctx)
    return status, json.loads(raw.decode())


def _audit_events(ctx):
    try:
        lines = open(ctx.audit.path).read().splitlines()
    except (IOError, OSError):
        return []
    return [json.loads(l) for l in lines]


class TestBootmenuStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = self._mkdtemp()
        # No other boot-menu processes on the box by default.
        patcher = mock.patch("nfupgrader.api.bootmenu.list_processes", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mkdtemp(self):
        import tempfile
        return tempfile.mkdtemp()

    def test_status_installed_idle(self):
        ctx = make_ctx(self.tmp, running=False)
        sid = _session_fixture(ctx)
        status, data = _get(ctx, sid, "/api/bootmenu/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["available"], True)
        self.assertEqual(data["greyed"], False)
        self.assertEqual(data["reason"], "")
        self.assertEqual(data["other"], "none")
        self.assertEqual(data["other_pids"], [])
        self.assertEqual(data["session"], {"state": "none", "pid": None,
                                           "started_at": None, "exit_code": None})
        self.assertEqual(data["busy"], False)

    def test_status_not_installed_greyed(self):
        ctx = install_ctx(self.tmp, installed=False)
        sid = _session_fixture(ctx)
        status, data = _get(ctx, sid, "/api/bootmenu/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["greyed"], True)
        self.assertEqual(data["available"], False)
        self.assertEqual(data["reason"], "netFLEX is not installed")

    def test_status_preview_refused(self):
        ctx = make_ctx(self.tmp, running=False)
        ctx.state["preview"] = "before"
        sid = _session_fixture(ctx)
        status, data = _get(ctx, sid, "/api/bootmenu/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["available"], False)
        self.assertEqual(data["greyed"], True)
        self.assertEqual(data["reason"], "preview mode")

    def test_status_foreign_and_leftover(self):
        ctx = make_ctx(self.tmp, running=False)
        sid = _session_fixture(ctx)
        with mock.patch("nfupgrader.api.bootmenu.classify_others",
                        return_value=("foreign", [111])):
            status, data = _get(ctx, sid, "/api/bootmenu/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["available"], False)
        # R5: not greyed -- the tab opens and shows the reason, not hidden.
        self.assertEqual(data["greyed"], False)
        self.assertEqual(data["reason"], "someone else is running the boot menu")
        self.assertEqual(data["other"], "foreign")
        self.assertEqual(data["other_pids"], [111])

        with mock.patch("nfupgrader.api.bootmenu.classify_others",
                        return_value=("leftover", [222, 333])):
            status, data = _get(ctx, sid, "/api/bootmenu/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["available"], False)
        # R5: not greyed -- the tab opens with an End it button.
        self.assertEqual(data["greyed"], False)
        self.assertEqual(data["reason"], "a boot menu from an earlier run is still on this box")
        self.assertEqual(data["other"], "leftover")
        self.assertEqual(data["other_pids"], [222, 333])

    def test_status_install_running(self):
        ctx = install_ctx(self.tmp, installed=True, running=True)
        sid = _session_fixture(ctx)
        status, data = _get(ctx, sid, "/api/bootmenu/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["available"], False)
        # R5: not greyed -- only "netFLEX is not installed" and "preview mode" grey the tab.
        self.assertEqual(data["greyed"], False)
        self.assertEqual(data["reason"], "an install or upgrade is running")

    def test_status_own_session_running_is_available(self):
        ctx = make_ctx(self.tmp, running=False)
        ctx.state["bootmenu"] = StubSession(running=True,
                                            state={"state": "running", "pid": 42,
                                                   "started_at": 1.0, "exit_code": None})
        sid = _session_fixture(ctx)
        status, data = _get(ctx, sid, "/api/bootmenu/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["available"], True)
        self.assertEqual(data["greyed"], False)
        self.assertEqual(data["reason"], "")
        self.assertEqual(data["session"]["state"], "running")
        self.assertEqual(data["session"]["pid"], 42)


class TestBootmenuEnd(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch("nfupgrader.api.bootmenu.list_processes", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_end_session(self):
        ctx = make_ctx(self.tmp, running=False)
        session = StubSession(running=True)
        ctx.state["bootmenu"] = session
        sid = _session_fixture(ctx)
        status, data = _post(ctx, sid, "/api/bootmenu/end")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ended": True})
        self.assertEqual(session.end_calls, 1)
        self.assertIsNone(session.end_reason)
        # The end is audited once, by the session's on_exit hook (installed by
        # httpd), not by the API route.
        events = _audit_events(ctx)
        self.assertEqual([e for e in events if e["event"] == "bootmenu_end"], [])

    def test_end_session_not_running_409(self):
        ctx = make_ctx(self.tmp, running=False)
        ctx.state["bootmenu"] = StubSession(running=False)
        sid = _session_fixture(ctx)
        status, data = _post(ctx, sid, "/api/bootmenu/end")
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "no boot menu session is running")

    def test_end_session_no_session_object_409(self):
        ctx = make_ctx(self.tmp, running=False)
        sid = _session_fixture(ctx)
        status, data = _post(ctx, sid, "/api/bootmenu/end")
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "no boot menu session is running")


class TestBootmenuEndLeftover(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def _pidfile_path(self, ctx):
        return os.path.join(ctx.inclogdir, bootmenu.PIDFILE_NAME)

    def test_end_leftover_only_when_leftover(self):
        ctx = make_ctx(self.tmp, running=False)
        sid = _session_fixture(ctx)
        with mock.patch("nfupgrader.api.bootmenu.list_processes", return_value=[]), \
             mock.patch("nfupgrader.api.bootmenu.classify_others",
                        return_value=("none", [])):
            status, data = _post(ctx, sid, "/api/bootmenu/end-leftover")
        self.assertEqual(status, 409)

        with mock.patch("nfupgrader.api.bootmenu.list_processes", return_value=[]), \
             mock.patch("nfupgrader.api.bootmenu.classify_others",
                        return_value=("foreign", [999])):
            status, data = _post(ctx, sid, "/api/bootmenu/end-leftover")
        self.assertEqual(status, 409)

    def test_end_leftover_kills_and_cleans_up(self):
        ctx = make_ctx(self.tmp, running=False)
        pidfile = self._pidfile_path(ctx)
        open(pidfile, "w").write("1 2 3\n")
        sid = _session_fixture(ctx)
        killed = []
        with mock.patch("nfupgrader.api.bootmenu.list_processes", return_value=[]), \
             mock.patch("nfupgrader.api.bootmenu.classify_others",
                        return_value=("leftover", [321, 654])), \
             mock.patch("nfupgrader.api.os.kill",
                        side_effect=lambda pid, sig: killed.append((pid, sig))):
            status, data = _post(ctx, sid, "/api/bootmenu/end-leftover")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ended": True})
        self.assertEqual(killed, [(321, signal.SIGKILL), (654, signal.SIGKILL)])
        self.assertFalse(os.path.exists(pidfile))
        events = _audit_events(ctx)
        leftovers = [e for e in events if e["event"] == "bootmenu_end_leftover"]
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(leftovers[0]["pids"], [321, 654])

    def test_end_leftover_rechecks_right_before_killing(self):
        # Availability said "leftover", but by the time we re-check (right
        # before killing) it is no longer so: refuse, kill nothing.
        ctx = make_ctx(self.tmp, running=False)
        sid = _session_fixture(ctx)
        calls = {"n": 0}

        def classify(*_a, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return "leftover", [321]
            return "foreign", [321]

        with mock.patch("nfupgrader.api.bootmenu.list_processes", return_value=[]), \
             mock.patch("nfupgrader.api.bootmenu.classify_others", side_effect=classify), \
             mock.patch("nfupgrader.api.os.kill") as kill:
            status, data = _post(ctx, sid, "/api/bootmenu/end-leftover")
        self.assertEqual(status, 409)
        kill.assert_not_called()
        self.assertGreaterEqual(calls["n"], 2)


class TestBootmenuGuards(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_guards_refuse_while_open(self):
        ctx = make_ctx(self.tmp, running=False)
        ctx.state["bootmenu"] = StubSession(running=True)
        sid = _session_fixture(ctx)

        status, data = _post(ctx, sid, "/api/launch", {"mode": "install"})
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "the boot menu is open")

        status, data = _post(ctx, sid, "/api/install/reset", {"reason": "because"})
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "the boot menu is open")

        status, data = _post(ctx, sid, "/api/checks/fix", {"name": "whatever"})
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "the boot menu is open")

    def test_guards_do_not_trigger_when_not_running(self):
        ctx = make_ctx(self.tmp, running=False)
        ctx.state["bootmenu"] = StubSession(running=False)
        sid = _session_fixture(ctx)
        status, data = _post(ctx, sid, "/api/install/reset", {"reason": "because"})
        # Not refused by the boot-menu guard; whatever else happens is a
        # different concern (e.g. "no unfinished install to reset").
        self.assertNotEqual(data.get("error"), "the boot menu is open")


if __name__ == "__main__":
    unittest.main()
