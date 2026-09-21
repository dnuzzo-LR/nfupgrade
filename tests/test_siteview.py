import os
import tempfile
import unittest
from nfupgrader import siteview, site
from nfupgrader.host import FakeHost, RunResult


def _tier1_responses(app_up=False):
    return {
        ("rpm", "-qi", "netFLEX-CORE"): RunResult(0, "Version     : 5.4.0\nRelease     : 29\n", ""),
        ("rpm", "-qi", "netFLEX-CORE-PATCH"): RunResult(1, "", ""),
        ("rpm", "-qi", "netFLEX-CORE-IPATCH"): RunResult(1, "", ""),
        ("readlink", "/usr/cnc"): RunResult(0, "/usr/inc/inc54.029\n", ""),
        ("readlink", "/usr/cnc_stage"): RunResult(1, "", ""),
        ("readlink", "/usr/cnc_saved"): RunResult(1, "", ""),
        ("test", "-f", "/usr/cnc/.CNC_UP"): RunResult(0 if app_up else 1, "", ""),
        ("cat", "/usr/cnc/.GR_STATUS"): RunResult(1, "", ""),
        ("test", "-f", "/usr/cnc/grestore.pid"): RunResult(1, "", ""),
        ("df", "-kP"): RunResult(0, "H\n/d 1 1 1 5% /usr4\n", ""),
        ("true",): RunResult(0, "", ""),   # reachability probe
    }


class TestSiteView(unittest.TestCase):
    def _cnfg(self, tmp):
        p = os.path.join(tmp, "cnc.cnfg")
        open(p, "w").write("fep1 1 fep2 2 1 0\nfep2 2 fep1 1 1 0\n")
        return p

    def test_local_full_peer_tier1(self):
        tmp = tempfile.mkdtemp()
        cnfg = self._cnfg(tmp)
        hosts = site.parse_cnfg(open(cnfg).read())
        local = FakeHost(_tier1_responses(app_up=True))
        peers = {"fep2": FakeHost(_tier1_responses(app_up=True))}
        view = siteview.gather_site(hosts, "fep1", cnfg, local,
                                    lambda n: peers[n])
        rows = {r["name"]: r for r in view["hosts"]}
        self.assertTrue(rows["fep1"]["is_local"])
        self.assertTrue(rows["fep1"]["reachable"])
        self.assertIsNotNone(rows["fep1"]["info"]["app_detail"])   # local = full
        self.assertFalse(rows["fep2"]["is_local"])
        self.assertTrue(rows["fep2"]["reachable"])
        self.assertIsNone(rows["fep2"]["info"]["app_detail"])      # peer = tier1 only

    def test_duplicate_host_lines_collapse_to_one_row(self):
        # cnc.cnfg lists one line per mate relationship, so a host repeats.
        tmp = tempfile.mkdtemp()
        cnfg = os.path.join(tmp, "cnc.cnfg")
        open(cnfg, "w").write("m60 1 m61 1 1 9\nm61 1 m60 1 0 0\n"
                              "m60 1 m62 2 1 9\nm62 2 m60 1 0 0\n")
        hosts = site.parse_cnfg(open(cnfg).read())
        local = FakeHost(_tier1_responses())
        peers = {"m61": FakeHost(_tier1_responses()), "m62": FakeHost(_tier1_responses())}
        view = siteview.gather_site(hosts, "m60", cnfg, local, lambda n: peers[n])
        names = [r["name"] for r in view["hosts"]]
        self.assertEqual(names, ["m60", "m61", "m62"])   # m60 once, not twice
        self.assertEqual(sum(1 for r in view["hosts"] if r["is_local"]), 1)

    def test_unreachable_peer_marked(self):
        tmp = tempfile.mkdtemp()
        cnfg = self._cnfg(tmp)
        hosts = site.parse_cnfg(open(cnfg).read())
        local = FakeHost(_tier1_responses())
        dead = FakeHost({("true",): RunResult(255, "", "unreachable")})
        view = siteview.gather_site(hosts, "fep1", cnfg, local, lambda n: dead)
        rows = {r["name"]: r for r in view["hosts"]}
        self.assertFalse(rows["fep2"]["reachable"])
        self.assertIsNone(rows["fep2"]["info"])
        self.assertEqual(view["local"], "fep1")


if __name__ == "__main__":
    unittest.main()
