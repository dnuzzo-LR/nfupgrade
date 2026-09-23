import json
import os
import tempfile
import unittest
from nfupgrader import api, httpd, snapjobs
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


class TestLaunchCwd(unittest.TestCase):
    def test_explicit_env_wins(self):
        self.assertEqual(httpd.resolve_launch_cwd("/opt/depot", "/x/my.cfg"), "/opt/depot")

    def test_defaults_to_user_config_dir(self):
        self.assertEqual(httpd.resolve_launch_cwd(None, "/opt/apt/upgrade/my.cfg"),
                         "/opt/apt/upgrade")

    def test_none_when_nothing_known(self):
        self.assertIsNone(httpd.resolve_launch_cwd(None, None))


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

        def begin(runner, launch, read_status, client, audit=None):
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
        def begin(runner, launch, read_status, client, audit=None, on_error=None):
            on_error("boom")
            return "20260923_140000_before"
        ctx = launch_ctx(self.tmp, runner=FakeRunner())
        ctx = ctx._replace(state={})
        status, body = httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform,
                                                begin=begin)
        self.assertEqual(status, 202)
        self.assertEqual(ctx.state["launch_error"], "boom")

    def test_async_launch_error_cleared_on_next_launch(self):
        def begin_ok(runner, launch, read_status, client, audit=None, on_error=None):
            return "20260923_140100_before"
        ctx = launch_ctx(self.tmp, runner=FakeRunner())
        ctx = ctx._replace(state={"launch_error": "stale"})
        httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform, begin=begin_ok)
        self.assertNotIn("launch_error", ctx.state)

    def test_no_state_dict_does_not_pass_on_error_or_raise(self):
        seen = {}

        def begin(runner, launch, read_status, client, audit=None):
            seen["called"] = True
            launch()
            return "20260923_140200_before"
        ctx = launch_ctx(self.tmp, runner=FakeRunner())  # ctx.state is None
        status, body = httpd.launch_side_effect(ctx, "upgrade", "ip", perform=self.perform,
                                                begin=begin)
        self.assertEqual(status, 202)
        self.assertTrue(seen["called"])


if __name__ == "__main__":
    unittest.main()
