import unittest
from nfupgrader.host import Host, FakeHost, RunResult


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


if __name__ == "__main__":
    unittest.main()
