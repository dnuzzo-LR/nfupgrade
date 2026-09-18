import os
import tempfile
import unittest
from nfupgrader import launcher
from nfupgrader.host import FakeHost, RunResult


class TestLauncher(unittest.TestCase):
    def test_build_argv_stage(self):
        argv = launcher.build_launch_argv("/opt/nfupgrader/nf_upgrade", "/tmp/eff.cfg", "stage")
        self.assertEqual(argv, ["/opt/nfupgrader/nf_upgrade", "-c", "/tmp/eff.cfg", "-s"])

    def test_build_argv_upgrade(self):
        argv = launcher.build_launch_argv("/x/nf_upgrade", "/x/eff.cfg", "upgrade")
        self.assertEqual(argv[-1], "-u")

    def test_build_argv_rejects_bad_mode(self):
        with self.assertRaises(ValueError):
            launcher.build_launch_argv("/x/nf_upgrade", "/x/eff.cfg", "sideways")

    def test_effective_config_sources_original_and_forces_no_prompt(self):
        src = os.path.join(tempfile.mkdtemp(), "user.cfg")
        open(src, "w").write('INCLOGDIR="/usr/cnc/logs"\nNEW_GENERIC="54.1"\n')
        eff = launcher.write_effective_config(src, os.path.join(tempfile.mkdtemp(), "eff.cfg"))
        body = open(eff).read()
        self.assertIn('. "{}"'.format(src), body)
        self.assertIn("PROMPTFORVALIDATE=NO", body)

    def test_liveness_true_when_pgrep_finds_process(self):
        fake = FakeHost({("pgrep", "-f", "nf_upgrader"): RunResult(0, "4321\n", "")})
        self.assertTrue(launcher.upgrader_running(fake))

    def test_liveness_false_when_pgrep_empty(self):
        fake = FakeHost({("pgrep", "-f", "nf_upgrader"): RunResult(1, "", "")})
        self.assertFalse(launcher.upgrader_running(fake))


if __name__ == "__main__":
    unittest.main()
