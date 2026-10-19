import os
import tempfile
import unittest
from nfupgrader import installstate
from nfupgrader.host import FakeHost, RunResult


class TestInstallState(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def put(self, name, text):
        open(os.path.join(self.d, name), "w").write(text)

    def test_info(self):
        self.put(".h1_INSTALL_INFO", "LOADDIR=/usr4/inc5.3.002\nVERSION=5.3.0.02\n")
        self.assertEqual(installstate.read_install_info("h1", self.d)["LOADDIR"], "/usr4/inc5.3.002")
        self.assertEqual(installstate.read_install_info("h2", self.d), {})

    def test_unfinished(self):
        self.assertFalse(installstate.unfinished("h1", self.d))
        self.put(".h1_INSTALL_STATE", "isrpm\n");  self.assertTrue(installstate.unfinished("h1", self.d))
        self.put(".h1_INSTALL_STATE", "isdone\n"); self.assertFalse(installstate.unfinished("h1", self.d))

    def test_host_mode(self):
        rpm = {("rpm", "-q", "netFLEX-CORE"): RunResult(0, "x", "")}
        self.assertEqual(installstate.host_mode(FakeHost({}), "h1", self.d), "install")
        self.assertEqual(installstate.host_mode(FakeHost(rpm), "h1", self.d), "upgrade")
        self.put(".h1_INSTALL_STATE", "isdatabase\n")
        self.assertEqual(installstate.host_mode(FakeHost(rpm), "h1", self.d), "install")


if __name__ == "__main__":
    unittest.main()
