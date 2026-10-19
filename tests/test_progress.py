import os
import tempfile
import unittest
from nfupgrader import progress


class TestComputeProgress(unittest.TestCase):
    def test_midway_running(self):
        p = progress.compute_progress("stage", "swscopycnc", running=True)
        self.assertEqual(p["phase"], "stage")
        self.assertEqual(p["current"], "swscopycnc")
        self.assertEqual(p["completed"], 3)   # 3 states done before swscopycnc
        self.assertEqual(p["total"], 6)
        self.assertEqual(p["status"], "running")
        self.assertIn("Copy", p["label"])

    def test_terminal_is_done_regardless_of_process(self):
        p = progress.compute_progress("stage", "swscomplete", running=False)
        self.assertEqual(p["status"], "done")
        self.assertEqual(p["completed"], 6)
        self.assertEqual(p["total"], 6)

    def test_nonterminal_no_process_is_died(self):
        p = progress.compute_progress("upgrade", "swipreboot", running=False)
        self.assertEqual(p["status"], "died")

    def test_unknown_state_reports_completed_zero(self):
        p = progress.compute_progress("stage", "weird", running=True)
        self.assertEqual(p["completed"], 0)
        self.assertEqual(p["current"], "weird")


class TestReadProgress(unittest.TestCase):
    def test_reads_state_file_and_uses_liveness(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, ".h1_STATE"), "w").write("swscopycnc\n")
        p = progress.read_progress("h1", "stage", d, running=True)
        self.assertEqual(p["current"], "swscopycnc")
        self.assertEqual(p["completed"], 3)
        self.assertEqual(p["status"], "running")

    def test_missing_state_file_is_not_started(self):
        d = tempfile.mkdtemp()
        p = progress.read_progress("h1", "stage", d, running=False)
        self.assertEqual(p["status"], "not_started")
        self.assertIsNone(p["current"])
        self.assertEqual(p["completed"], 0)



class TestPhaseInference(unittest.TestCase):
    def test_upgrade_staged_not_running_is_not_started(self):
        self.assertEqual(progress.compute_progress("upgrade", "swscomplete", False)["status"],
                         "not_started")
        self.assertEqual(progress.compute_progress("upgrade", "swscomplete", True)["status"],
                         "running")

    def test_infer_phase(self):
        for st in ("swistart", "swicopycnc", "postinstall", "swidone"):
            self.assertEqual(progress.infer_phase(st), "upgrade", st)
        for st in ("swsstart", "swscopycnc", "swscomplete", None, "weird"):
            self.assertEqual(progress.infer_phase(st), "stage", st)

    def test_read_state(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(progress.read_state("h1", d))
        open(os.path.join(d, ".h1_STATE"), "w").write("swistart\n")
        self.assertEqual(progress.read_state("h1", d), "swistart")

    def test_install_reads_its_own_state_file(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, ".h1_STATE"), "w").write("swscopycnc\n")
        open(os.path.join(d, ".h1_INSTALL_STATE"), "w").write("ispostinstall\n")
        p = progress.read_progress("h1", "install", d, running=False)
        self.assertEqual((p["current"], p["status"], p["step"], p["total"]), ("ispostinstall", "died", 9, 13))
        self.assertEqual(progress.read_progress("h1", "stage", d, False)["current"], "swscopycnc")

    def test_install_done(self):
        p = progress.compute_progress("install", "isdone", False)
        self.assertEqual((p["status"], p["completed"]), ("done", 13))


class TestStepNumber(unittest.TestCase):
    def test_step_is_one_based(self):
        self.assertEqual(progress.compute_progress("stage", "swsstart", True)["step"], 1)
        self.assertEqual(progress.compute_progress("stage", "swscopycnc", True)["step"], 4)
        self.assertEqual(progress.compute_progress("stage", "swscopycnc", False)["step"], 4)  # died there

    def test_done_is_last_step(self):
        p = progress.compute_progress("stage", "swscomplete", False)
        self.assertEqual((p["step"], p["total"]), (6, 6))
        self.assertEqual(progress.compute_progress("upgrade", "swidone", False)["step"], 9)

    def test_not_started_or_unknown_has_no_step(self):
        self.assertIsNone(progress.compute_progress("upgrade", "swscomplete", False)["step"])
        self.assertIsNone(progress.compute_progress("stage", "weird", True)["step"])
        d = tempfile.mkdtemp()
        self.assertIsNone(progress.read_progress("h1", "stage", d, running=False)["step"])

if __name__ == "__main__":
    unittest.main()
