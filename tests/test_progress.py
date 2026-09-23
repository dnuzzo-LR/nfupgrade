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

if __name__ == "__main__":
    unittest.main()
