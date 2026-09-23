import time
import unittest
from nfupgrader.host import Host, FakeHost, RunResult, build_ssh_argv, SshHost


class TestHost(unittest.TestCase):
    def test_local_run_captures_rc_and_stdout(self):
        r = Host().run(["sh", "-c", "echo hello; exit 0"])
        self.assertEqual(r.rc, 0)
        self.assertEqual(r.out.strip(), "hello")

    def test_local_run_captures_nonzero_and_stderr(self):
        r = Host().run(["sh", "-c", "echo oops 1>&2; exit 3"])
        self.assertEqual(r.rc, 3)
        self.assertIn("oops", r.err)

    def test_fakehost_returns_canned_result_by_argv(self):
        fake = FakeHost({("cat", "/x"): RunResult(0, "data", "")})
        r = fake.run(["cat", "/x"])
        self.assertEqual(r.out, "data")
        self.assertEqual(r.rc, 0)

    def test_fakehost_unknown_argv_defaults_to_rc1(self):
        r = FakeHost({}).run(["nope"])
        self.assertEqual(r.rc, 1)

    def test_local_run_survives_undecodable_bytes(self):
        r = Host().run(["printf", "\\377ok"])
        self.assertEqual(r.rc, 0)
        self.assertTrue(r.out.endswith("ok"))
        self.assertIn("�", r.out)

    def test_timeout_kills_whole_pipeline(self):
        # A grandchild (sleep) holding the pipe must not keep us waiting past
        # the timeout: the whole process group is killed.
        t0 = time.time()
        r = Host().run(["sh", "-c", "sleep 5 | cat"], timeout=1)
        elapsed = time.time() - t0
        self.assertEqual(r.rc, 124)
        self.assertEqual(r.err, "timeout: sh -c sleep 5 | cat")
        self.assertLess(elapsed, 3)

    def test_missing_binary_is_127(self):
        r = Host().run(["/nonexistent/nfu-no-such-binary"])
        self.assertEqual(r.rc, 127)


class TestSsh(unittest.TestCase):
    def test_build_ssh_argv_quotes_remote_command(self):
        argv = build_ssh_argv("bep1", ["rpm", "-qi", "netFLEX-CORE"])
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("bep1", argv)
        self.assertEqual(argv[-1], "rpm -qi netFLEX-CORE")

    def test_build_ssh_argv_quotes_spaces(self):
        argv = build_ssh_argv("bep1", ["sh", "-c", "echo a b"])
        self.assertEqual(argv[-1], "sh -c 'echo a b'")

    def test_sshhost_runs_through_local_exec(self):
        r = SshHost("nonexistent.invalid").run(["true"])
        self.assertIsNotNone(r.rc)


if __name__ == "__main__":
    unittest.main()
