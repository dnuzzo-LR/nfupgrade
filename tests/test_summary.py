"""#9 (2026-11-11 full-upgrade test): nothing said the upgrade would kill user
sessions or move PostgreSQL 15 -> 17. upgrade_summary says what this upgrade
will do, read-only, from the box, the saved config and the staged load."""
import unittest

from nfupgrader import summary
from nfupgrader.host import FakeHost, RunResult


PS = ("    PID TTY          TIME CMD\n"
      "   4100 pts/1    00:00:01 USER\n"
      "   4101 pts/2    00:00:00 USER\n"
      "   4102 ?        00:00:03 cmm_main\n"
      "   4103 pts/3    00:00:00 USERX\n")


def host(pg_cur="15", pg_target="17", up=True, users=PS, du="22020096\t/usr/cnc/rdb/data\n",
         rdb_features=None):
    r = {
        ("rpm", "-qi", "netFLEX-CORE"): RunResult(0, "Version     : 5.4.0.29\n", ""),
        ("rpm", "-qi", "netFLEX-CORE-PATCH"): RunResult(0, "Version     : 5.4.0.36\n", ""),
        ("test", "-f", "/usr/cnc/features/netFLEX"): RunResult(0, "", ""),
        ("ps", "-e"): RunResult(0, users, ""),
        ("du", "-sk", "/usr/cnc/rdb/data"): RunResult(0, du, ""),
        ("test", "-f", "/usr/cnc/.CNC_UP"): RunResult(0 if up else 1, "", ""),
    }
    if rdb_features is not None:
        r[("cat", "/usr/cnc/features/rdb")] = RunResult(0, rdb_features, "")
    if pg_cur:
        r[("cat", "/usr/cnc/rdb/data/PG_VERSION")] = RunResult(0, pg_cur + "\n", "")
    if pg_target:
        r[("cat", "/usr/cnc_stage/bin/dbupgrade.sh")] = RunResult(
            0, "#!/bin/sh\n# comment\nUPGRADE_VERSION={}\n".format(pg_target), "")
    return FakeHost(r)


CFG = {"NEW_GENERIC": "5.4.1", "NEW_LOAD": "28", "RESTARTINC": "YES"}


class TestUpgradeSummary(unittest.TestCase):
    def test_full(self):
        s = summary.upgrade_summary(host(), CFG)
        self.assertEqual((s["from"], s["to"]), ("5.4.0.36", "5.4.1.28"))
        self.assertEqual(s["users"], 2)               # USER only, not USERX
        self.assertEqual(s["rdb_gb"], 21.0)
        self.assertEqual(s["pg"], {"current": "15", "target": "17", "upgrade": True})
        self.assertEqual(s["boot"], {"will_boot": True, "why": "netFLEX is up now and RESTARTINC is not NO"})

    def test_same_pg_major(self):
        self.assertFalse(summary.upgrade_summary(host(pg_target="15"), CFG)["pg"]["upgrade"])

    def test_pg_unknown(self):
        s = summary.upgrade_summary(host(pg_target=None), CFG)
        self.assertEqual(s["pg"], {"current": "15", "target": None, "upgrade": None})

    def test_no_boot(self):
        s = summary.upgrade_summary(host(up=False), CFG)
        self.assertEqual(s["boot"], {"will_boot": False, "why": "netFLEX is not up now"})
        s = summary.upgrade_summary(host(), dict(CFG, RESTARTINC="NO"))
        self.assertEqual(s["boot"], {"will_boot": False, "why": "RESTARTINC=NO"})

    def test_external_data_dir_and_remote(self):
        h = host(rdb_features="data_directory=/usr4/rdbdata\n")
        h.responses[("cat", "/usr4/rdbdata/PG_VERSION")] = RunResult(0, "15\n", "")
        h.responses[("du", "-sk", "/usr4/rdbdata")] = RunResult(0, "1048576\t/usr4/rdbdata\n", "")
        s = summary.upgrade_summary(h, CFG)
        self.assertEqual((s["rdb_gb"], s["pg"]["current"]), (1.0, "15"))
        s = summary.upgrade_summary(host(rdb_features="rdb_server_location=remote\n"), CFG)
        self.assertEqual((s["rdb_gb"], s["pg"]["current"]), (None, None))

    def test_unreadable_pieces_are_none(self):
        s = summary.upgrade_summary(FakeHost({}), {})
        self.assertEqual((s["from"], s["to"], s["users"], s["rdb_gb"]), (None, None, None, None))
