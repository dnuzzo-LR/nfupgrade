import json
import os
import tempfile
import time
import threading
import unittest
from unittest import mock
from nfupgrader import api, httpd, launcher, snapjobs
from nfupgrader.audit import AuditLog
from nfupgrader.host import FakeHost, RunResult


class TestHelpers(unittest.TestCase):
    def test_parse_cookies(self):
        self.assertEqual(httpd.parse_cookies("nfu_sid=abc; other=1")["nfu_sid"], "abc")

    def test_parse_cookies_empty(self):
        self.assertEqual(httpd.parse_cookies(""), {})

    def test_client_ip_prefers_socket(self):
        self.assertEqual(httpd.client_ip_from(("10.1.1.9", 5555)), "10.1.1.9")


class TestUrlHost(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual(httpd.resolve_url_host("0.0.0.0", "r9dev19", "h"), "r9dev19")

    def test_wildcard_bind_uses_hostname(self):
        self.assertEqual(httpd.resolve_url_host("0.0.0.0", None, "r9dev19"), "r9dev19")

    def test_empty_bind_uses_hostname(self):
        self.assertEqual(httpd.resolve_url_host("", None, "r9dev19"), "r9dev19")

    def test_specific_bind_used_verbatim(self):
        self.assertEqual(httpd.resolve_url_host("127.0.0.1", None, "h"), "127.0.0.1")


class TestTunnelCommand(unittest.TestCase):
    def test_loopback_bind_uses_hostname_and_sudo_user(self):
        self.assertEqual(httpd.tunnel_command("127.0.0.1", 8099, None, "holmvm32", "dan"),
                         "ssh -L 8099:127.0.0.1:8099 dan@holmvm32")

    def test_no_user_when_not_under_sudo(self):
        self.assertEqual(httpd.tunnel_command("127.0.0.1", 8099, None, "holmvm32", None),
                         "ssh -L 8099:127.0.0.1:8099 holmvm32")

    def test_sudo_user_root_is_left_out(self):
        self.assertEqual(httpd.tunnel_command("127.0.0.1", 8099, None, "h", "root"),
                         "ssh -L 8099:127.0.0.1:8099 h")

    def test_wildcard_bind_tunnels_to_loopback(self):
        self.assertEqual(httpd.tunnel_command("0.0.0.0", 8099, None, "h", None),
                         "ssh -L 8099:127.0.0.1:8099 h")

    def test_specific_bind_address_is_the_tunnel_target(self):
        self.assertEqual(httpd.tunnel_command("10.1.2.3", 8099, None, "h", None),
                         "ssh -L 8099:10.1.2.3:8099 h")

    def test_url_host_override_is_the_ssh_host(self):
        self.assertEqual(httpd.tunnel_command("127.0.0.1", 8099, "r9dev19.lab", "h", None),
                         "ssh -L 8099:127.0.0.1:8099 r9dev19.lab")


class TestStartupBanner(unittest.TestCase):
    def test_loopback_prints_tunnel_and_local_url(self):
        text = "\n".join(httpd.startup_banner(
            "127.0.0.1", 8099, "http://127.0.0.1:8099/auth?token=T",
            "ssh -L 8099:127.0.0.1:8099 h"))
        self.assertIn("    ssh -L 8099:127.0.0.1:8099 h", text)
        self.assertIn("    http://127.0.0.1:8099/auth?token=T", text)
        self.assertNotIn("WARNING", text)

    def test_points_at_bastion_route_when_forwarding_is_refused(self):
        for bind in ("127.0.0.1", "0.0.0.0"):
            text = "\n".join(httpd.startup_banner(
                bind, 8099, "http://h:8099/auth?token=T", "ssh -L 8099:127.0.0.1:8099 h"))
            self.assertIn("administratively prohibited", text)
            self.assertIn("bastion", text)

    def test_network_bind_prints_url_and_tunnel_alternative(self):
        text = "\n".join(httpd.startup_banner(
            "0.0.0.0", 8099, "http://h:8099/auth?token=T",
            "ssh -L 8099:127.0.0.1:8099 h"))
        self.assertIn("WARNING", text)
        self.assertIn("    http://h:8099/auth?token=T", text)
        self.assertIn("    ssh -L 8099:127.0.0.1:8099 h", text)
        self.assertIn("    http://127.0.0.1:8099/auth?token=T", text)


class TestDepotDirs(unittest.TestCase):
    def test_explicit_env_wins(self):
        self.assertEqual(httpd.resolve_depot_dirs("/a:/b", "/start"), ["/a", "/b"])

    def test_explicit_env_skips_empty_segments(self):
        self.assertEqual(httpd.resolve_depot_dirs("/a::/b:", "/start"), ["/a", "/b"])

    def test_defaults_to_start_dir_then_opt_apt_upgrade(self):
        self.assertEqual(httpd.resolve_depot_dirs(None, "/opt/nfupgrader"),
                         ["/opt/nfupgrader", "/opt/apt/upgrade"])
        self.assertEqual(httpd.resolve_depot_dirs("", "/opt/nfupgrader"),
                         ["/opt/nfupgrader", "/opt/apt/upgrade"])

    def test_default_not_duplicated(self):
        self.assertEqual(httpd.resolve_depot_dirs(None, "/opt/apt/upgrade"), ["/opt/apt/upgrade"])

    def test_no_start_dir(self):
        self.assertEqual(httpd.resolve_depot_dirs(None, None), ["/opt/apt/upgrade"])


class TestIncLogDir(unittest.TestCase):
    def test_explicit_env_wins(self):
        self.assertEqual(httpd.resolve_inclogdir("/usr/cnc/logs", "/opt/nfupgrader"),
                         "/usr/cnc/logs")

    def test_defaults_to_start_dir_logs(self):
        self.assertEqual(httpd.resolve_inclogdir(None, "/opt/nfupgrader"), "/opt/nfupgrader/logs")
        self.assertEqual(httpd.resolve_inclogdir("", "/opt/nfupgrader"), "/opt/nfupgrader/logs")

    def test_created_when_missing(self):
        start = tempfile.mkdtemp()
        d = httpd.ensure_dir(httpd.resolve_inclogdir(None, start))
        self.assertTrue(os.path.isdir(os.path.join(start, "logs")))
        self.assertEqual(httpd.ensure_dir(d), d)       # already there: fine


class TestStaticHeaders(unittest.TestCase):
    def test_static_files_are_not_cached_stale(self):
        for ext in (".html", ".js", ".css"):
            h = dict(httpd.static_headers(ext, 10))
            self.assertEqual(h["Cache-Control"], "no-cache")
            self.assertEqual(h["Content-Length"], "10")
        self.assertEqual(dict(httpd.static_headers(".js", 1))["Content-Type"],
                         "application/javascript")


class TestRunSh(unittest.TestCase):
    def test_records_start_dir_before_cd(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.sh")
        text = open(path, encoding="utf-8").read()
        self.assertIn("NFU_START_DIR", text)
        self.assertLess(text.index("NFU_START_DIR"), text.index('cd "$(dirname "$0")"'))


class TestLaunchCwd(unittest.TestCase):
    def test_explicit_env_wins(self):
        self.assertEqual(httpd.resolve_launch_cwd("/opt/depot", "/x/my.cfg"), "/opt/depot")

    def test_defaults_to_user_config_dir(self):
        self.assertEqual(httpd.resolve_launch_cwd(None, "/opt/apt/upgrade/my.cfg"),
                         "/opt/apt/upgrade")

    def test_none_when_nothing_known(self):
        self.assertIsNone(httpd.resolve_launch_cwd(None, None))


class TestPerformLaunchCwd(unittest.TestCase):
    """#2: the stage runs from the CORE RPM's folder (the extracted UPGRADEPKG
    bundle beside it), unless NFU_LAUNCH_CWD says otherwise."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.calls = []
        self._orig = launcher.launch
        launcher.launch = lambda path, cfgp, mode, cwd=None: self.calls.append((mode, cwd))

    def tearDown(self):
        launcher.launch = self._orig

    def ctx(self, **kw):
        user = os.path.join(self.tmp, "u.cfg")
        with open(user, "w") as fh:
            fh.write("DEPOTFILE={}/netFLEX-CORE-5.4.1.28-1.rhel8.x86_64.rpm\n".format(self.tmp))
        return launch_ctx(self.tmp)._replace(state={"user_config": user},
                                             effective_config=os.path.join(self.tmp, "eff.cfg"), **kw)

    def test_runs_from_core_rpm_folder(self):
        httpd.perform_launch(self.ctx(launch_cwd="/opt/cfgdir"), "stage")
        self.assertEqual(self.calls, [("stage", self.tmp)])

    def test_explicit_env_wins(self):
        httpd.perform_launch(self.ctx(launch_cwd="/opt/b", launch_cwd_env="/opt/b"), "stage")
        self.assertEqual(self.calls, [("stage", "/opt/b")])


def launch_ctx(tmp, runner=None, host=None):
    return api.Context(
        host=host or FakeHost(), hostname="h1", inclogdir=tmp, phase="stage",
        tracefile=os.path.join(tmp, "trace"), auth=None,
        audit=AuditLog(os.path.join(tmp, "audit.jsonl")), webdir=tmp,
        nf_upgrade_path="/bin/true", runner=runner)


class FakeRunner(object):
    pass


class TestLaunchSideEffect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.performed = []

    def perform(self, ctx, mode):
        self.performed.append(mode)

    def events(self):
        return [json.loads(l)["event"] for l in open(os.path.join(self.tmp, "audit.jsonl"))]

    def test_stage_launches_directly(self):
        ctx = launch_ctx(self.tmp, runner=FakeRunner())
        status, body = httpd.launch_side_effect(ctx, "stage", "ip", perform=self.perform,
                                                begin=None)
        self.assertEqual((status, body), (202, {"accepted": True, "mode": "stage"}))
        self.assertEqual(self.performed, ["stage"])

    def test_direct_launch_failure_is_500_and_audited(self):
        def boom(ctx, mode):
            raise OSError('bad "path"')
        status, body = httpd.launch_side_effect(launch_ctx(self.tmp), "stage", "ip",
                                                perform=boom)
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], 'launch failed: bad "path"')
        self.assertIn("launch_error", self.events())

    def test_upgrade_goes_through_before_snapshot(self):
        seen = {}

        def begin(runner, launch, read_status, client, audit=None, **kw):
            seen.update(runner=runner, client=client)
            launch()
            return "20260923_140000_before"
        runner = FakeRunner()
        ctx = launch_ctx(self.tmp, runner=runner)
        status, body = httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform,
                                                begin=begin)
        self.assertEqual(status, 202)
        self.assertEqual(body, {"accepted": True, "mode": "upgrade",
                                "snapshot": "20260923_140000_before"})
        self.assertIs(seen["runner"], runner)
        self.assertEqual(self.performed, ["upgrade"])

    def test_upgrade_busy_is_409(self):
        def begin(*a, **kw):
            raise snapjobs.Busy()
        status, body = httpd.launch_side_effect(launch_ctx(self.tmp, runner=FakeRunner()),
                                                "upgrade", "ip", perform=self.perform,
                                                begin=begin)
        self.assertEqual(status, 409)
        self.assertEqual(self.performed, [])

    def test_upgrade_without_runner_launches_directly(self):
        status, body = httpd.launch_side_effect(launch_ctx(self.tmp), "upgrade", "ip",
                                                perform=self.perform)
        self.assertEqual(status, 202)
        self.assertEqual(self.performed, ["upgrade"])

    def test_accepted_launch_remembers_phase(self):
        ctx = launch_ctx(self.tmp)._replace(state={})
        httpd.launch_side_effect(ctx, "stage", "ip", perform=self.perform)
        self.assertEqual(ctx.state["phase"], "stage")

        def begin(runner, launch, read_status, client, audit=None, **kw):
            return "20260923_140000_before"
        ctx = launch_ctx(self.tmp, runner=FakeRunner())._replace(state={})
        httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform, begin=begin)
        self.assertEqual(ctx.state["phase"], "upgrade")

    def test_failed_launch_does_not_change_phase(self):
        def boom(ctx, mode):
            raise OSError("nope")
        ctx = launch_ctx(self.tmp)._replace(state={"phase": "stage"})
        httpd.launch_side_effect(ctx, "upgrade", "ip", perform=boom)
        self.assertEqual(ctx.state["phase"], "stage")

    def test_upgrade_status_uses_config_logdir(self):
        updir = tempfile.mkdtemp()
        cfgp = os.path.join(self.tmp, "u.cfg")
        open(cfgp, "w").write("INCLOGDIR=%s\n" % updir)
        open(os.path.join(updir, ".h1_STATE"), "w").write("swidone\n")
        host = FakeHost({("pgrep", "-f", "nf_upgrader"): RunResult(1, "", "")})
        ctx = launch_ctx(self.tmp, host=host)._replace(state={"user_config": cfgp})
        self.assertEqual(httpd.upgrade_status(ctx), "done")

    def test_upgrade_status_reads_upgrade_phase(self):
        host = FakeHost({("pgrep", "-f", "nf_upgrader"): RunResult(1, "", "")})
        open(os.path.join(self.tmp, ".h1_STATE"), "w").write("swidone\n")
        self.assertEqual(httpd.upgrade_status(launch_ctx(self.tmp, host=host)), "done")

    def test_upgrade_busy_audits_launch_rejected(self):
        def begin(*a, **kw):
            raise snapjobs.Busy()
        status, body = httpd.launch_side_effect(launch_ctx(self.tmp, runner=FakeRunner()),
                                                "upgrade", "ip", perform=self.perform,
                                                begin=begin)
        self.assertEqual(status, 409)
        self.assertIn("launch_rejected", self.events())

    def test_async_launch_error_surfaced_in_state(self):
        def begin(runner, launch, read_status, client, audit=None, on_error=None, **kw):
            on_error("boom")
            return "20260923_140000_before"
        ctx = launch_ctx(self.tmp, runner=FakeRunner())
        ctx = ctx._replace(state={})
        status, body = httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform,
                                                begin=begin)
        self.assertEqual(status, 202)
        self.assertEqual(ctx.state["launch_error"], "boom")

    def test_async_launch_error_cleared_on_next_launch(self):
        def begin_ok(runner, launch, read_status, client, audit=None, on_error=None, **kw):
            return "20260923_140100_before"
        ctx = launch_ctx(self.tmp, runner=FakeRunner())
        ctx = ctx._replace(state={"launch_error": "stale"})
        httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform, begin=begin_ok)
        self.assertNotIn("launch_error", ctx.state)

    def test_stale_launch_error_cleared_on_direct_launch(self):
        for runner, mode in ((FakeRunner(), "stage"), (None, "upgrade"), (None, "stage")):
            ctx = launch_ctx(self.tmp, runner=runner)._replace(
                state={"launch_error": "stale", "user_config": "/x.cfg"})
            status, _ = httpd.launch_side_effect(ctx, mode, "ip", perform=self.perform)
            self.assertEqual(status, 202)
            self.assertIn("launched_at", ctx.state)
            del ctx.state["launched_at"]
            self.assertEqual(ctx.state, {"user_config": "/x.cfg", "phase": mode,
                                          "log_follow": None})

    def test_refused_while_fix_in_progress(self):
        def begin(*a, **kw):
            raise AssertionError("before snapshot must not start during a fix")
        self.assertTrue(api._FIX_LOCK.acquire(False))
        try:
            results = [httpd.launch_side_effect(launch_ctx(self.tmp, runner=runner), mode, "ip",
                                                perform=self.perform, begin=begin)
                       for runner, mode in ((None, "stage"), (FakeRunner(), "upgrade"))]
        finally:
            api._FIX_LOCK.release()
        self.assertEqual(results, [(409, {"error": "check fix in progress"})] * 2)
        self.assertEqual(self.performed, [])
        rej = [json.loads(l) for l in open(os.path.join(self.tmp, "audit.jsonl"))]
        self.assertEqual([(e["event"], e["reason"]) for e in rej],
                         [("launch_rejected", "check fix in progress")] * 2)

    def test_fix_cannot_start_while_launch_spawns(self):
        held = []

        def perform(ctx, mode):
            held.append(api.fix_in_progress())
        httpd.launch_side_effect(launch_ctx(self.tmp), "stage", "ip", perform=perform)

        def begin(runner, launch, read_status, client, audit=None, **kw):
            held.append(api.fix_in_progress())
            return "20260923_140300_before"
        httpd.launch_side_effect(launch_ctx(self.tmp, runner=FakeRunner()), "upgrade", "ip",
                                 perform=perform, begin=begin)

        def boom(ctx, mode):
            raise OSError("nope")
        httpd.launch_side_effect(launch_ctx(self.tmp), "stage", "ip", perform=boom)
        self.assertEqual(held, [True, True])
        self.assertFalse(api.fix_in_progress())

    def test_refused_while_boot_menu_open(self):
        # I1: re-checked under the fix lock, so a boot menu started after the
        # dispatcher's check still stops the spawn.
        class Running(object):
            def running(self):
                return api.fix_in_progress()   # only true when asked under the lock

        def begin(*a, **kw):
            raise AssertionError("before snapshot must not start with the boot menu open")
        results = []
        for runner, mode in ((None, "stage"), (FakeRunner(), "upgrade")):
            ctx = launch_ctx(self.tmp, runner=runner)._replace(state={"bootmenu": Running()})
            results.append(httpd.launch_side_effect(ctx, mode, "ip", perform=self.perform,
                                                    begin=begin))
            self.assertNotIn("launched_at", ctx.state)
        inst = []
        ctx = launch_ctx(self.tmp)._replace(state={"bootmenu": Running()})
        results.append(httpd.reset_side_effect(ctx, "ip", perform_inst=lambda c, a: inst.append(a)))
        self.assertEqual(results, [(409, {"error": "the boot menu is open"})] * 3)
        self.assertEqual(self.performed, [])
        self.assertEqual(inst, [])
        self.assertFalse(api.fix_in_progress())
        rej = [json.loads(l) for l in open(os.path.join(self.tmp, "audit.jsonl"))]
        self.assertEqual([(e["event"], e["reason"]) for e in rej],
                         [("launch_rejected", "the boot menu is open")] * 3)

    def test_no_state_dict_does_not_pass_on_error_or_raise(self):
        seen = {}

        def begin(runner, launch, read_status, client, audit=None, **kw):
            seen["called"] = True
            launch()
            return "20260923_140200_before"
        ctx = launch_ctx(self.tmp, runner=FakeRunner())  # ctx.state is None
        status, body = httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform,
                                                begin=begin)
        self.assertEqual(status, 202)
        self.assertTrue(seen["called"])

    def test_accepted_direct_launch_records_launched_at(self):
        ctx = launch_ctx(self.tmp)._replace(state={})
        before = api._now()
        httpd.launch_side_effect(ctx, "stage", "ip", perform=self.perform)
        self.assertGreaterEqual(ctx.state["launched_at"], before)
        self.assertLessEqual(ctx.state["launched_at"], api._now())

    def test_launch_grace_ignores_wall_clock(self):
        # An install can step the system clock back; the grace must not follow it.
        self.assertIs(api._now, time.monotonic)
        ctx = launch_ctx(self.tmp)._replace(state={})
        with mock.patch.object(time, "time", return_value=0.0):
            httpd.launch_side_effect(ctx, "stage", "ip", perform=self.perform)
        self.assertGreater(ctx.state["launched_at"], 0.0)

    def test_accepted_upgrade_launch_records_launched_at(self):
        def begin(runner, launch, read_status, client, audit=None, **kw):
            launch()
            return "20260923_140400_before"
        ctx = launch_ctx(self.tmp, runner=FakeRunner())._replace(state={})
        httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform, begin=begin)
        self.assertIn("launched_at", ctx.state)

    def test_failed_launch_does_not_record_launched_at(self):
        def boom(ctx, mode):
            raise OSError("nope")
        ctx = launch_ctx(self.tmp)._replace(state={})
        status, _ = httpd.launch_side_effect(ctx, "stage", "ip", perform=boom)
        self.assertEqual(status, 500)
        self.assertNotIn("launched_at", ctx.state)

    def test_busy_launch_does_not_record_launched_at(self):
        def begin(*a, **kw):
            raise snapjobs.Busy()
        ctx = launch_ctx(self.tmp, runner=FakeRunner())._replace(state={})
        status, _ = httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform,
                                             begin=begin)
        self.assertEqual(status, 409)
        self.assertNotIn("launched_at", ctx.state)

    def test_no_state_dict_launched_at_does_not_raise(self):
        ctx = launch_ctx(self.tmp)  # ctx.state is None
        status, body = httpd.launch_side_effect(ctx, "stage", "ip", perform=self.perform)
        self.assertEqual(status, 202)


class TestInstallSideEffect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def events(self):
        return [json.loads(l)["event"] for l in open(os.path.join(self.tmp, "audit.jsonl"))]

    def test_install_launch_skips_snapshot(self):
        def begin(*a, **kw):
            raise AssertionError("install must not take a snapshot")
        seen = []

        def perform_inst(ctx, action):
            seen.append(action)
        ctx = launch_ctx(self.tmp, runner=FakeRunner())
        status, body = httpd.launch_side_effect(ctx, "install", "ip", begin=begin,
                                                action="install", perform_inst=perform_inst)
        self.assertEqual(status, 202)
        self.assertEqual(body, {"accepted": True, "mode": "install", "action": "install"})
        self.assertEqual(seen, ["install"])

    def test_resume_action_passed_through(self):
        def disp(method, path, query, body, cookies, ip, ctx):
            return 202, [("Content-Type", "application/json")], json.dumps(
                {"accepted": True, "mode": "install", "action": "resume"}).encode("utf-8")
        seen = []

        def perform_inst(ctx, action):
            seen.append(action)

        def launch(ctx, mode, ip, action=None):
            return httpd.launch_side_effect(ctx, mode, ip, action=action,
                                            perform_inst=perform_inst)
        ctx = launch_ctx(self.tmp, runner=FakeRunner())
        httpd.safe_dispatch("POST", "/api/launch", {}, b'{"mode": "install"}', {}, "ip", ctx,
                           dispatch=disp, launch=launch)
        self.assertEqual(seen, ["resume"])

    def test_reset_side_effect(self):
        seen = []

        def perform_inst(ctx, action):
            seen.append(action)
        ctx = launch_ctx(self.tmp)._replace(state={})
        status, body = httpd.reset_side_effect(ctx, "ip", perform_inst=perform_inst)
        self.assertEqual(status, 202)
        self.assertEqual(body, {"accepted": True, "mode": "install", "action": "reset"})
        self.assertEqual(seen, ["reset"])
        self.assertEqual(ctx.state["phase"], "install")

    def test_install_spawn_failure_is_500(self):
        def boom(ctx, action):
            raise OSError('bad "path"')
        ctx = launch_ctx(self.tmp, runner=FakeRunner())
        status, body = httpd.launch_side_effect(ctx, "install", "ip", action="install",
                                                perform_inst=boom)
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], 'launch failed: bad "path"')
        self.assertIn("launch_error", self.events())


class BlockingPopen(object):
    """A Popen stand-in whose process 'exits' (with rc) when finish() is called."""

    def __init__(self, rc):
        self.rc = rc
        self.waited = False
        self._done = threading.Event()

    def finish(self):
        self._done.set()

    def poll(self):
        return self.rc if self._done.is_set() else None

    def wait(self, timeout=None):
        self._done.wait()
        self.waited = True
        return self.rc


class TestPatchSideEffect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_accepted_sets_job_and_follow(self):
        calls = []
        popen = BlockingPopen(0)
        self.addCleanup(popen.finish)
        ctx = launch_ctx(self.tmp)._replace(state={"phase": "stage"}, nf_patch_path="/o/nf_patch")

        def perform(c, rpm, web_tar=None):
            calls.append(rpm)
            return popen
        status, body = httpd.patch_side_effect(ctx, "ip", "/d/p.rpm", version="5.4.1", perform=perform)
        self.assertEqual((status, body), (202, {"accepted": True, "path": "/d/p.rpm"}))
        self.assertEqual(calls, ["/d/p.rpm"])
        self.assertEqual(ctx.state["patch_job"], {"popen": popen, "path": "/d/p.rpm",
                                                  "version": "5.4.1", "client": "ip",
                                                  "web": None})
        self.assertEqual(ctx.state["log_follow"], "patch")
        self.assertEqual(ctx.state["phase"], "stage")
        self.assertFalse(api.fix_in_progress())

    def test_patch_side_effect_passes_web(self):
        calls = []
        popen = BlockingPopen(0)
        self.addCleanup(popen.finish)
        ctx = launch_ctx(self.tmp)._replace(state={}, nf_patch_path="/o/nf_patch")

        def perform(c, rpm, web_tar=None):
            calls.append((rpm, web_tar))
            return popen
        status, body = httpd.patch_side_effect(ctx, "ip", "/d/p.rpm", version="5.4.1",
                                               perform=perform, web_tar="/d/W.rhel8.tar")
        self.assertEqual(status, 202)
        self.assertEqual(calls, [("/d/p.rpm", "/d/W.rhel8.tar")])
        self.assertEqual(ctx.state["patch_job"]["web"], "/d/W.rhel8.tar")

    def test_exit_is_reaped_and_audited_without_a_patches_request(self):
        # nf_patch must be waited for as soon as it exits, or it lingers as a
        # <defunct> zombie until someone happens to open the Patches tab.
        popen = BlockingPopen(0)
        self.addCleanup(popen.finish)
        ctx = launch_ctx(self.tmp)._replace(state={}, nf_patch_path="/o/nf_patch")
        status, _ = httpd.patch_side_effect(ctx, "ip", "/d/p.rpm", version="5.4.1",
                                            perform=lambda c, r, web_tar=None: popen)
        self.assertEqual(status, 202)
        popen.finish()
        deadline = time.time() + 5
        while "patch_job" in ctx.state and time.time() < deadline:
            time.sleep(0.01)
        self.assertNotIn("patch_job", ctx.state)
        self.assertTrue(popen.waited)
        recs = [json.loads(l) for l in open(os.path.join(self.tmp, "audit.jsonl"))]
        done = [r for r in recs if r["event"] == "patch_install_done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["version"], "5.4.1")

    def test_busy_lock_is_409(self):
        ctx = launch_ctx(self.tmp)._replace(state={})
        self.assertTrue(api._FIX_LOCK.acquire(False))
        try:
            status, body = httpd.patch_side_effect(ctx, "ip", "/d/p.rpm",
                                                   perform=lambda c, r, web_tar=None: self.fail("spawned"))
        finally:
            api._FIX_LOCK.release()
        self.assertEqual((status, body), (409, {"error": "another action is running"}))
        self.assertNotIn("patch_job", ctx.state)
        self.assertEqual(len(self._refused()), 1)

    def test_something_running_is_409(self):
        host = FakeHost({("pgrep", "-f", "nf_patch"): RunResult(0, "5\n", ""),
                         ("cat", "/proc/5/cmdline"): RunResult(0, "nf_patch\0-f\0x\0", "")})
        ctx = launch_ctx(self.tmp, host=host)._replace(state={})
        status, body = httpd.patch_side_effect(ctx, "ip", "/d/p.rpm",
                                               perform=lambda c, r, web_tar=None: self.fail("spawned"))
        self.assertEqual(status, 409)
        self.assertEqual(self._refused()[0]["reason"], "another action is running")
        self.assertFalse(api.fix_in_progress())

    def test_spawn_failure_is_500_and_audited(self):
        def perform(c, rpm, web_tar=None):
            raise OSError("boom")
        ctx = launch_ctx(self.tmp)._replace(state={})
        status, body = httpd.patch_side_effect(ctx, "ip", "/d/p.rpm", perform=perform)
        self.assertEqual(status, 500)
        self.assertNotIn("patch_job", ctx.state)
        recs = [json.loads(l) for l in open(os.path.join(self.tmp, "audit.jsonl"))]
        failed = [r for r in recs if r["event"] == "patch_install_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["path"], "/d/p.rpm")
        self.assertEqual(failed[0]["error"], "boom")
        self.assertEqual(failed[0]["client"], "ip")
        self.assertIn("version", failed[0])
        self.assertFalse(api.fix_in_progress())

    def _refused(self):
        p = os.path.join(self.tmp, "audit.jsonl")
        if not os.path.exists(p):
            return []
        return [json.loads(l) for l in open(p)
                if json.loads(l)["event"] == "patch_install_refused"]

    def test_bootmenu_open_is_409_and_audited(self):
        class Running(object):
            def running(self):
                return True
        ctx = launch_ctx(self.tmp)._replace(state={"bootmenu": Running()})
        status, body = httpd.patch_side_effect(ctx, "ip", "/d/p.rpm",
                                               perform=lambda c, r, web_tar=None: self.fail("spawned"))
        self.assertEqual((status, body), (409, {"error": "the boot menu is open"}))
        ref = self._refused()
        self.assertEqual(len(ref), 1)
        self.assertEqual((ref[0]["path"], ref[0]["reason"], ref[0]["client"]),
                         ("/d/p.rpm", "the boot menu is open", "ip"))
        self.assertFalse(api.fix_in_progress())

    def test_snapshot_busy_is_409(self):
        class Busy(object):
            def busy(self):
                return True
        ctx = launch_ctx(self.tmp, runner=Busy())._replace(state={})
        status, body = httpd.patch_side_effect(ctx, "ip", "/d/p.rpm",
                                               perform=lambda c, r, web_tar=None: self.fail("spawned"))
        self.assertEqual((status, body), (409, {"error": "report snapshot in progress"}))
        self.assertEqual(len(self._refused()), 1)

    def test_safe_dispatch_routes_202(self):
        seen = []

        def disp(method, path, query, body, cookies, ip, ctx):
            out = json.dumps({"accepted": True, "path": "/d/p.rpm", "version": "5.4.1",
                              "web": "/d/W.rhel8.tar"}).encode()
            return 202, [], out

        def patch(ctx, ip, path, version=None, web_tar=None):
            seen.append((path, version, web_tar))
            return 202, {"accepted": True, "path": path}
        ctx = launch_ctx(self.tmp)
        status, headers, out = httpd.safe_dispatch(
            "POST", "/api/patches/install", {}, b"{}", {}, "ip", ctx, dispatch=disp, patch=patch)
        self.assertEqual(status, 202)
        self.assertEqual(seen, [("/d/p.rpm", "5.4.1", "/d/W.rhel8.tar")])
        self.assertEqual(json.loads(out.decode()), {"accepted": True, "path": "/d/p.rpm"})

    def test_stage_launch_and_reset_clear_log_follow(self):
        ctx = launch_ctx(self.tmp)._replace(state={"log_follow": "patch"})
        httpd.launch_side_effect(ctx, "stage", "ip", perform=lambda c, m: None)
        self.assertIsNone(ctx.state["log_follow"])
        for fn in (lambda c: httpd.launch_side_effect(c, "install", "ip", action="install",
                                                       perform_inst=lambda c, a: None),
                   lambda c: httpd.reset_side_effect(c, "ip", perform_inst=lambda c, a: None)):
            ctx = launch_ctx(self.tmp)._replace(state={"log_follow": "patch"})
            fn(ctx)
            self.assertIsNone(ctx.state["log_follow"])


class TestLoadInterruptedFix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "audit.jsonl")
        self.audit = AuditLog(self.path)

    def _write(self, entries):
        with open(self.path, "w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def _events(self):
        return [json.loads(l) for l in open(self.path)]

    def test_none_when_no_unfinished_fix(self):
        self._write([{"ts": "t1", "event": "check_fix_start", "name": "a", "kind": "rpm"},
                     {"ts": "t2", "event": "check_fix", "name": "a", "kind": "rpm", "ok": True}])
        self.assertIsNone(httpd.load_interrupted_fix(self.audit, self.path))
        self.assertNotIn("check_fix_interrupted", [e["event"] for e in self._events()])

    def test_missing_audit_file_returns_none(self):
        self.assertIsNone(httpd.load_interrupted_fix(
            self.audit, os.path.join(self.tmp, "nope.jsonl")))

    def test_records_and_returns_interrupted_fix_once(self):
        self._write([{"ts": "t1", "event": "check_fix_start", "name": "pkg-ksh", "kind": "rpm"}])
        result = httpd.load_interrupted_fix(self.audit, self.path)
        self.assertEqual(result, {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"})
        interrupted = [e for e in self._events() if e["event"] == "check_fix_interrupted"]
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0]["name"], "pkg-ksh")
        self.assertEqual(interrupted[0]["kind"], "rpm")



class TestSafeDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_exception_becomes_500_json(self):
        def boom(*a):
            raise KeyError("x")
        import io
        import sys
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            status, headers, out = httpd.safe_dispatch("GET", "/api/info", {}, b"", {}, "ip",
                                                       launch_ctx(self.tmp), dispatch=boom)
            logged = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(status, 500)
        self.assertIn(("Content-Type", "application/json"), headers)
        self.assertEqual(json.loads(out.decode()), {"error": "internal error: KeyError: 'x'"})
        self.assertIn("Traceback", logged)
        self.assertIn("/api/info", logged)

    def test_passes_through_and_runs_launch_side_effect(self):
        def disp(method, path, query, body, cookies, ip, ctx):
            return 202, [("Content-Type", "application/json")], b'{"accepted": true}'
        seen = []

        def side(ctx, mode, ip, action=None):
            seen.append((mode, ip))
            return 202, {"accepted": True, "mode": mode}
        ctx = launch_ctx(self.tmp)
        r = httpd.safe_dispatch("POST", "/api/launch", {}, b'{"mode": "stage"}', {}, "ip", ctx,
                                dispatch=disp, launch=side)
        self.assertEqual(seen, [("stage", "ip")])
        self.assertEqual((r[0], json.loads(r[2].decode())),
                         (202, {"accepted": True, "mode": "stage"}))
        r = httpd.safe_dispatch("GET", "/api/x", {}, b"", {}, "ip", ctx, dispatch=disp,
                                launch=side)
        self.assertEqual((r[0], r[2]), (202, b'{"accepted": true}'))
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()


class TestUpgradeResume(unittest.TestCase):
    """#3: Resume rewrites "<step> failed" to "<step>" and re-runs the upgrade
    with APP_WAS_RUNNING from what the tool recorded at the original launch."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, ".h1_STATE")
        with open(self.state, "w") as fh:
            fh.write("swibootinc failed\n")
        self.calls = []

    def perform(self, ctx, mode, env=None):
        with open(self.state) as fh:
            self.calls.append((mode, env, fh.read().strip()))

    def read_state(self):
        with open(self.state) as fh:
            return fh.read().strip()

    def test_resume_rewrites_state_and_launches_with_boot(self):
        ctx = launch_ctx(self.tmp)._replace(state={"log_follow": "patch"})
        status, body = httpd.resume_side_effect(
            ctx, "ip", {"state": "swibootinc", "boot": True}, perform=self.perform)
        self.assertEqual(status, 202)
        self.assertEqual(body, {"accepted": True, "mode": "upgrade", "action": "resume",
                                "state": "swibootinc", "boot": True})
        self.assertEqual(self.calls, [("upgrade", {"APP_WAS_RUNNING": "YES"}, "swibootinc")])
        self.assertEqual((ctx.state["phase"], ctx.state["log_follow"]), ("upgrade", None))
        self.assertIn("upgrade_resumed", [json.loads(l)["event"]
                                          for l in open(os.path.join(self.tmp, "audit.jsonl"))])

    def test_resume_without_boot(self):
        httpd.resume_side_effect(launch_ctx(self.tmp), "ip", {"state": "swibootinc", "boot": False},
                                 perform=self.perform)
        self.assertEqual(self.calls[0][1], {"APP_WAS_RUNNING": "NO"})

    def test_state_changed_since_accept_refused(self):
        with open(self.state, "w") as fh:
            fh.write("swicopycnc failed\n")
        status, body = httpd.resume_side_effect(
            launch_ctx(self.tmp), "ip", {"state": "swibootinc", "boot": True}, perform=self.perform)
        self.assertEqual(status, 409)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.read_state(), "swicopycnc failed")

    def test_launch_error_restores_failed_state(self):
        def boom(ctx, mode, env=None):
            raise OSError("no such file")
        status, body = httpd.resume_side_effect(
            launch_ctx(self.tmp), "ip", {"state": "swibootinc", "boot": True}, perform=boom)
        self.assertEqual(status, 500)
        self.assertEqual(self.read_state(), "swibootinc failed")

    def test_safe_dispatch_routes_resume(self):
        seen = []

        def disp(method, path, query, body, cookies, ip, ctx):
            return 202, [], json.dumps({"accepted": True, "mode": "upgrade", "action": "resume",
                                        "state": "swibootinc", "boot": False}).encode()

        def resume(ctx, ip, accepted):
            seen.append(accepted)
            return 202, {"accepted": True}
        status, _, _ = httpd.safe_dispatch("POST", "/api/upgrade/resume", {}, b"{}", {}, "ip",
                                           launch_ctx(self.tmp), dispatch=disp, resume=resume)
        self.assertEqual(status, 202)
        self.assertEqual(seen[0]["state"], "swibootinc")


class TestUpgradeLaunchRecord(unittest.TestCase):
    """#3/#7: a fresh Upgrade launch records whether netFLEX was up (the test
    nf_upgrader's doswistart uses: .CNC_UP or .MUX_UP) and its before snapshot;
    continuing an interrupted upgrade takes no new before and pairs its after
    with the recorded one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.performed = []
        self.followed = []
        self._follow = snapjobs.follow_upgrade
        snapjobs.follow_upgrade = lambda runner, rs, before, **kw: self.followed.append(before)

    def tearDown(self):
        snapjobs.follow_upgrade = self._follow

    def perform(self, ctx, mode, env=None):
        self.performed.append((mode, env))

    def record(self):
        with open(os.path.join(self.tmp, ".nfupgrader_h1_upgrade.json")) as fh:
            return json.load(fh)

    def state(self, value):
        with open(os.path.join(self.tmp, ".h1_STATE"), "w") as fh:
            fh.write(value + "\n")

    def begin(self, runner, launch, read_status, client, audit=None, **kw):
        self.begin_kw = kw
        kw["on_before"]("20261111-191100-before")
        launch()
        return "20261111-191100-before"

    def test_fresh_upgrade_records_app_and_before(self):
        self.state("swscomplete")
        h = FakeHost({("test", "-f", "/usr/cnc/.MUX_UP"): RunResult(0, "", "")})
        ctx = launch_ctx(self.tmp, runner=FakeRunner(), host=h)
        status, body = httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform,
                                                begin=self.begin)
        self.assertEqual(status, 202)
        self.assertEqual(self.record()["app_was_running"], True)
        self.assertEqual(self.record()["before"], "20261111-191100-before")
        self.assertIn("app_up", self.begin_kw)
        self.assertEqual(self.performed, [("upgrade", None)])

    def test_fresh_upgrade_without_runner_records_app_down(self):
        self.state("swscomplete")
        httpd.launch_side_effect(launch_ctx(self.tmp), "upgrade", "ip", perform=self.perform)
        self.assertEqual((self.record()["app_was_running"], self.record()["before"]), (False, None))

    def test_continue_interrupted_upgrade_takes_no_before(self):
        self.state("swicopycnc")
        with open(os.path.join(self.tmp, ".nfupgrader_h1_upgrade.json"), "w") as fh:
            json.dump({"app_was_running": True, "before": "B0"}, fh)
        ctx = launch_ctx(self.tmp, runner=FakeRunner())

        def no_begin(*a, **kw):
            raise AssertionError("no before snapshot when continuing")
        status, body = httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform,
                                                begin=no_begin)
        self.assertEqual(status, 202)
        self.assertEqual(body, {"accepted": True, "mode": "upgrade", "continued": "swicopycnc"})
        self.assertEqual(self.performed, [("upgrade", None)])
        self.assertEqual(self.followed, ["B0"])
        self.assertEqual(self.record(), {"app_was_running": True, "before": "B0"})   # untouched

    def test_resume_follows_with_recorded_before(self):
        self.state("swibootinc failed")
        with open(os.path.join(self.tmp, ".nfupgrader_h1_upgrade.json"), "w") as fh:
            json.dump({"app_was_running": True, "before": "B0"}, fh)
        status, _ = httpd.resume_side_effect(launch_ctx(self.tmp, runner=FakeRunner()), "ip",
                                             {"state": "swibootinc", "boot": True},
                                             perform=self.perform)
        self.assertEqual(status, 202)
        self.assertEqual(self.followed, ["B0"])

    def test_perform_launch_never_records(self):
        orig = launcher.launch
        launcher.launch = lambda *a, **kw: None
        try:
            httpd.perform_launch(launch_ctx(self.tmp), "upgrade")
        finally:
            launcher.launch = orig
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".nfupgrader_h1_upgrade.json")))
