import unittest
from nfupgrader import localinfo

RPM_OUT = """Name        : netFLEX-CORE
Version     : 5.4.0
Release     : 29
Architecture: x86_64
"""

DF_OUT = """Filesystem     1024-blocks     Used Available Capacity Mounted on
/dev/sda4         73400320 46000000  27400320      63% /usr4
/dev/sda2         11534336  9000000   2534336      79% /usr2
tmpfs              8000000    10000   7990000       1% /tmp
"""


class TestParsers(unittest.TestCase):
    def test_rpm_version_combines_version_release(self):
        self.assertEqual(localinfo.parse_rpm_version(RPM_OUT), "5.4.0-29")

    def test_rpm_version_missing_returns_none(self):
        self.assertIsNone(localinfo.parse_rpm_version("package not installed"))

    def test_df_parses_rows(self):
        rows = localinfo.parse_df(DF_OUT, keep=("/usr4", "/tmp"))
        mounts = {r["mount"]: r for r in rows}
        self.assertIn("/usr4", mounts)
        self.assertIn("/tmp", mounts)
        self.assertNotIn("/usr2", mounts)   # filtered out
        self.assertEqual(mounts["/usr4"]["use"], "63%")
        self.assertEqual(mounts["/usr4"]["avail_kb"], 27400320)

    def test_df_keep_none_returns_all(self):
        self.assertEqual(len(localinfo.parse_df(DF_OUT)), 3)

    def test_dbcheck_error_count(self):
        out = "checking...\nERROR: bad tie\nERROR: bad alarm\nok\n"
        self.assertEqual(localinfo.parse_dbcheck_errors(out), 2)

    def test_dbcheck_zero(self):
        self.assertEqual(localinfo.parse_dbcheck_errors("all clean\n"), 0)

    def test_parse_incinfo_json_becomes_dict(self):
        v = localinfo.parse_incinfo('{"release": "5.5.0", "up": true}')
        self.assertEqual(v["release"], "5.5.0")
        self.assertTrue(v["up"])

    def test_parse_incinfo_non_json_stays_string(self):
        self.assertEqual(localinfo.parse_incinfo("release : 5.5.0\n"), "release : 5.5.0")


import os
import tempfile
from nfupgrader.host import FakeHost, RunResult


def _cnfg(tmp):
    p = os.path.join(tmp, "cnc.cnfg")
    open(p, "w").write("fep1 1 fep2 2 1 0\nfep2 2 fep1 1 1 0\n"
                       "bep1 1 bep2 2 0 0\nbep2 2 bep1 1 0 0\n")
    return p


class TestGather(unittest.TestCase):
    def _host(self, app_up=True):
        r = {
            ("rpm", "-qi", "netFLEX-CORE"): RunResult(0, "Version     : 5.4.0\nRelease     : 29\n", ""),
            ("rpm", "-qi", "netFLEX-CORE-PATCH"): RunResult(1, "not installed", ""),
            ("rpm", "-qi", "netFLEX-CORE-IPATCH"): RunResult(1, "not installed", ""),
            ("readlink", "/usr/cnc"): RunResult(0, "/usr/inc/inc54.029\n", ""),
            ("readlink", "/usr/cnc_stage"): RunResult(0, "/usr/inc/inc54.129\n", ""),
            ("readlink", "/usr/cnc_saved"): RunResult(1, "", ""),
            ("test", "-f", "/usr/cnc/.CNC_UP"): RunResult(0 if app_up else 1, "", ""),
            ("cat", "/usr/cnc/.GR_STATUS"): RunResult(0, "COMPLETED\n", ""),
            ("test", "-f", "/usr/cnc/grestore.pid"): RunResult(1, "", ""),
            ("df", "-kP"): RunResult(0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                                        "/dev/sda4 73400320 46000000 27400320 63% /usr4\n", ""),
            ("/usr/cnc/bin/incinfo",): RunResult(0, "INC 5.4.0-29\n", ""),
            ("rdb", "version"): RunResult(0, "rdb 1.2.3\n", ""),
            ("dbcheck", "-AV"): RunResult(0, "clean\n", ""),
            ("/usr/cnc/mbin/nestat", "-B"): RunResult(0, "01 fep2  -> UP 7001 MSG WTR 5\n", ""),
        }
        return FakeHost(r)

    def test_gather_tier1_topology_and_versions(self):
        tmp = tempfile.mkdtemp()
        info = localinfo.gather(self._host(app_up=False), "fep1", _cnfg(tmp))
        self.assertEqual(info["topology"]["kind"], "multibox")
        self.assertEqual(info["topology"]["role"], "FEP")
        self.assertEqual(info["topology"]["mate"], "fep2")
        self.assertEqual(info["topology"]["host_count"], 4)
        self.assertEqual(info["versions"]["core"], "5.4.0-29")
        self.assertIsNone(info["versions"]["patch"])
        self.assertEqual(info["loads"]["installed"], "/usr/inc/inc54.029")
        self.assertIsNone(info["loads"]["saved"])
        self.assertFalse(info["app_up"])
        self.assertEqual(info["gr"]["transfer"], "COMPLETED")
        self.assertFalse(info["gr"]["restore"])
        self.assertTrue(any(d["mount"] == "/usr4" for d in info["disk"]))

    def test_gather_tier2_absent_when_app_down(self):
        tmp = tempfile.mkdtemp()
        info = localinfo.gather(self._host(app_up=False), "fep1", _cnfg(tmp))
        self.assertIsNone(info["app_detail"])
        self.assertIsNone(info["links"])

    def test_gather_tier2_present_when_app_up(self):
        tmp = tempfile.mkdtemp()
        info = localinfo.gather(self._host(app_up=True), "fep1", _cnfg(tmp))
        self.assertIsNotNone(info["app_detail"])
        self.assertIn("INC", info["app_detail"]["incinfo"])
        self.assertEqual(info["app_detail"]["dbcheck_errors"], 0)
        self.assertIn("fep2", info["links"])

    def test_gather_tier1_only_skips_tier2_even_when_app_up(self):
        tmp = tempfile.mkdtemp()
        info = localinfo.gather(self._host(app_up=True), "fep1", _cnfg(tmp), tier1_only=True)
        self.assertTrue(info["app_up"])          # tier-1 still reported
        self.assertIsNone(info["app_detail"])     # tier-2 skipped
        self.assertIsNone(info["links"])

    def test_gather_singlebox(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "cnc.cnfg")
        open(p, "w").write("fep1 1 fep1 1 1 0\n")
        info = localinfo.gather(self._host(), "fep1", p)
        self.assertEqual(info["topology"]["kind"], "singlebox")


if __name__ == "__main__":
    unittest.main()
