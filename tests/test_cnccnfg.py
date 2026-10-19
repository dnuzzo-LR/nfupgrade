import os, tempfile, unittest
from nfupgrader import cnccnfg, site

class TestRender(unittest.TestCase):
    def test_single(self):
        self.assertEqual(cnccnfg.render_cnc_cnfg("SINGLE", "h1"),
            "# SingleBox (MUST be MultiBox or Single)\n# SystemPrompt=netFLEX\n"
            "# SystemString=INTEGRATED NETWORK CONTROLLER\n# Master Machine Name\nh1\n"
            "# Mach Mach-# Mate Mate-# Mach-Type (CNC=1 Mux=0) Override-Link (CNC)\n")

    def test_bep_puts_fep_line_first(self):
        t = cnccnfg.render_cnc_cnfg("BEP", "b1", mate="f1", master="f1")
        self.assertTrue(t.startswith("# MultiBox"))
        self.assertTrue(t.endswith("\nf1\n# Mach Mach-# Mate Mate-# Mach-Type (CNC=1 Mux=0) Override-Link (CNC)\n"
                                   "f1 1 b1 1 1 9\nb1 1 f1 1 0 0\n"))

    def test_round_trips_through_site_parser(self):
        hosts = site.parse_cnfg(cnccnfg.render_cnc_cnfg("FEP", "f1", mate="b1", master="b1"))
        self.assertEqual([(h.name, h.role, h.mate) for h in hosts], [("f1", "FEP", "b1"), ("b1", "BEP", "f1")])
        self.assertEqual(site.local_host(hosts, "b1").role, "BEP")

    def test_rejects(self):
        for args in (("XEP", "h1"), ("FEP", "h1"), ("FEP", "h1", "h1", "h1"), ("FEP", "h1", "b 1", "h1"),
                     ("FEP", "h1", "b1", "zz")):
            with self.assertRaises(ValueError):
                cnccnfg.render_cnc_cnfg(*args)

    def test_from_values_strips(self):
        t = cnccnfg.render_from_values({"BOXTYPE": "FEP", "MATEHOST": " b1 ", "MASTERHOST": "f1"}, "f1")
        self.assertIn("f1 1 b1 1 1 9\n", t)

class TestEtcHosts(unittest.TestCase):
    def test_parse(self):
        text = ("127.0.0.1 localhost localhost.localdomain\n::1 localhost6\n# c\n\n"
                "10.0.0.2 nyc-bep1 bep1.example  # mate\n10.0.0.3 nyc-fep2\n10.0.0.9 nyc-bep1\n")
        self.assertEqual(cnccnfg.parse_etc_hosts(text),
                         [{"name": "nyc-bep1", "ip": "10.0.0.2"}, {"name": "bep1.example", "ip": "10.0.0.2"},
                          {"name": "nyc-fep2", "ip": "10.0.0.3"}])

    def test_read_missing(self):
        self.assertEqual(cnccnfg.read_etc_hosts("/nonexistent/hosts"), [])

if __name__ == "__main__":
    unittest.main()
