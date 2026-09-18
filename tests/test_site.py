import os
import unittest
from nfupgrader import site

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


class TestSite(unittest.TestCase):
    def test_parse_multibox_counts_and_roles(self):
        text = open(os.path.join(FIX, "cnc.multibox.cnfg")).read()
        hosts = site.parse_cnfg(text)
        self.assertEqual(len(hosts), 4)
        self.assertEqual(hosts[0].name, "fep1")
        self.assertEqual(hosts[0].role, "FEP")
        self.assertEqual(hosts[0].mate, "fep2")
        self.assertEqual(hosts[2].role, "BEP")

    def test_parse_ignores_comments_and_blanks(self):
        hosts = site.parse_cnfg("# c\n\nfep1 1 fep1 1 1 0\n")
        self.assertEqual(len(hosts), 1)

    def test_local_host_match(self):
        hosts = site.parse_cnfg(open(os.path.join(FIX, "cnc.multibox.cnfg")).read())
        self.assertEqual(site.local_host(hosts, "bep2").role, "BEP")

    def test_local_host_absent_returns_none(self):
        hosts = site.parse_cnfg(open(os.path.join(FIX, "cnc.singlebox.cnfg")).read())
        self.assertIsNone(site.local_host(hosts, "elsewhere"))


if __name__ == "__main__":
    unittest.main()
