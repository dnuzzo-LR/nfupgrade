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

    def _host(self, cmdlines):
        """FakeHost where pgrep returns the given pids and /proc/<pid>/cmdline
        holds each argv (NUL-separated, as the kernel exposes it)."""
        pids = sorted(cmdlines)
        responses = {("pgrep", "-f", "nf_upgrader"):
                     RunResult(0 if pids else 1, "".join("%d\n" % p for p in pids), "")}
        for pid, argv in cmdlines.items():
            responses[("cat", "/proc/%d/cmdline" % pid)] = \
                RunResult(0, "\0".join(argv) + "\0", "")
        return FakeHost(responses)

    def test_liveness_true_for_ksh_running_the_script(self):
        # nf_upgrade: nohup nice ${EXECDIR}/nf_upgrader ... ; the #!/usr/bin/ksh shebang
        # makes the kernel run "/usr/bin/ksh /opt/nfupgrader/nf_upgrader ...".
        fake = self._host({4321: ["/usr/bin/ksh", "/opt/nfupgrader/nf_upgrader",
                                  "-c", "/x.cfg", "-s"]})
        self.assertTrue(launcher.upgrader_running(fake))

    def test_liveness_true_for_direct_exec_and_shell_options(self):
        self.assertTrue(launcher.upgrader_running(
            self._host({7: ["/opt/nfupgrader/nf_upgrader", "-u"]})))
        self.assertTrue(launcher.upgrader_running(
            self._host({8: ["ksh", "-x", "./nf_upgrader", "-u"]})))

    def test_liveness_ignores_editors_pagers_and_greps(self):
        fake = self._host({
            11: ["vi", "/opt/nfupgrader/nf_upgrader"],
            12: ["less", "nf_upgrader"],
            13: ["tail", "-f", "/usr/cnc/logs/nf_upgrader.out"],
            14: ["grep", "-n", "nf_upgrader", "README.md"],
            15: ["/bin/bash", "-c", "cat nf_upgrader | wc -l"],
        })
        self.assertFalse(launcher.upgrader_running(fake))

    def test_liveness_true_if_any_candidate_is_the_upgrader(self):
        fake = self._host({11: ["vi", "nf_upgrader"],
                           12: ["/usr/bin/ksh", "/opt/nfupgrader/nf_upgrader", "-s"]})
        self.assertTrue(launcher.upgrader_running(fake))

    def test_liveness_false_when_process_vanished(self):
        fake = FakeHost({("pgrep", "-f", "nf_upgrader"): RunResult(0, "99\n", "")})
        # /proc/99/cmdline is unmocked -> rc 1 (process exited between the calls)
        self.assertFalse(launcher.upgrader_running(fake))

    def test_liveness_false_when_pgrep_empty(self):
        fake = FakeHost({("pgrep", "-f", "nf_upgrader"): RunResult(1, "", "")})
        self.assertFalse(launcher.upgrader_running(fake))


if __name__ == "__main__":
    unittest.main()
