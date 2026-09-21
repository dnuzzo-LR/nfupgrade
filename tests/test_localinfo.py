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


if __name__ == "__main__":
    unittest.main()
