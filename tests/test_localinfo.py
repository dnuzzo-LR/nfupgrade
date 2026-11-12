import os
import unittest
from nfupgrader import localinfo

DBCHECK_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "dbcheck")


#: `ps -e -o user=,comm=` output with and without a netFLEX process run by cmm.
PS_UP = "root     systemd\ncmm      dacs\ncmm      ksh\n"
PS_DOWN = "root     systemd\ncmm      ksh\npostgres postgres\n"


def dbcheck_out(name):
    """Canned `dbcheck -AV` stdout (tests/fixtures/dbcheck/<name>.out)."""
    with open(os.path.join(DBCHECK_DIR, name + ".out")) as f:
        return f.read()

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

    # dbcheck -AV output, from the printf()s in netFLEX cnc/tools/src/dbcheck.c.
    def test_dbcheck_clean_is_zero(self):
        # "0 errors found" mentions "error" but is the all-clear summary.
        self.assertEqual(localinfo.parse_dbcheck_errors(dbcheck_out("clean"), 0), 0)

    def test_dbcheck_count_is_the_summary_total(self):
        # Error lines and the per-type table must not be counted on top of Total.
        self.assertEqual(localinfo.parse_dbcheck_errors(dbcheck_out("errors3"), 1), 3)

    def test_dbcheck_blocked_audits_still_total(self):
        self.assertEqual(localinfo.parse_dbcheck_errors(dbcheck_out("blocked1"), 1), 1)

    def test_dbcheck_without_summary_is_unknown(self):
        self.assertIsNone(localinfo.parse_dbcheck_errors(dbcheck_out("initfail"), 1))
        self.assertIsNone(localinfo.parse_dbcheck_errors("", 0))

    def test_dbcheck_exit_code_must_agree(self):
        self.assertIsNone(localinfo.parse_dbcheck_errors(dbcheck_out("clean"), 1))
        self.assertIsNone(localinfo.parse_dbcheck_errors(dbcheck_out("errors3"), 0))

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


class TestAppState(unittest.TestCase):
    """netFLEX's own rule (boot_nms): up only while AT/sdi/dacs/screen/arm/bill/
    dump runs; .CNC_UP alone can be left behind by a forced stop."""

    def host(self, cnc_up, ps_out, ps_rc=0):
        return FakeHost({
            ("test", "-f", "/usr/cnc/.CNC_UP"): RunResult(0 if cnc_up else 1, "", ""),
            ("ps", "-e", "-o", "user=,comm="): RunResult(ps_rc, ps_out, ""),
        })

    def test_up_needs_flag_and_a_cmm_process(self):
        self.assertEqual(localinfo.app_state(self.host(True, PS_UP)), "up")

    def test_flag_without_processes_is_stale(self):
        self.assertEqual(localinfo.app_state(self.host(True, PS_DOWN)), "stale")

    def test_no_flag_is_down_even_with_processes(self):
        self.assertEqual(localinfo.app_state(self.host(False, PS_UP)), "down")

    def test_process_must_belong_to_cmm(self):
        # e.g. a support user's GNU screen session must not count
        self.assertEqual(localinfo.app_state(self.host(True, "dan      screen\nroot     dump\n")),
                         "stale")

    def test_every_boot_nms_name_counts_and_match_is_exact(self):
        for name in ("AT", "sdi", "dacs", "screen", "arm", "bill", "dump"):
            self.assertEqual(localinfo.app_state(self.host(True, "cmm %s\n" % name)), "up", name)
        self.assertEqual(localinfo.app_state(self.host(True, "cmm dacsproc\ncmm at\n")), "stale")

    def test_ps_failure_trusts_the_flag(self):
        self.assertEqual(localinfo.app_state(self.host(True, "", ps_rc=1)), "up")

    def test_incroot(self):
        h = FakeHost({("test", "-f", "/x/.CNC_UP"): RunResult(0, "", ""),
                      ("ps", "-e", "-o", "user=,comm="): RunResult(0, PS_UP, "")})
        self.assertEqual(localinfo.app_state(h, "/x"), "up")


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
            ("ps", "-e", "-o", "user=,comm="): RunResult(0, PS_UP if app_up else PS_DOWN, ""),
            ("cat", "/usr/cnc/.GR_STATUS"): RunResult(0, "COMPLETED\n", ""),
            ("test", "-f", "/usr/cnc/grestore.pid"): RunResult(1, "", ""),
            ("df", "-kP"): RunResult(0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                                        "/dev/sda4 73400320 46000000 27400320 63% /usr4\n", ""),
            ("/usr/cnc/bin/incinfo", "-j"): RunResult(0, '{"release": "5.4.0", "up": true}', ""),
            ("rdb", "version"): RunResult(0, "rdb 1.2.3\n", ""),
            ("dbcheck", "-AV"): RunResult(0, dbcheck_out("clean"), ""),
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
        self.assertEqual(info["app_detail"]["incinfo"]["release"], "5.4.0")
        self.assertEqual(info["app_detail"]["dbcheck_errors"], 0)
        self.assertIn("fep2", info["links"])

    def test_gather_stale_cnc_up_is_down(self):
        tmp = tempfile.mkdtemp()
        h = self._host(app_up=True)
        h.responses[("ps", "-e", "-o", "user=,comm=")] = RunResult(0, PS_DOWN, "")
        info = localinfo.gather(h, "fep1", _cnfg(tmp))
        self.assertFalse(info["app_up"])
        self.assertEqual(info["app_state"], "stale")
        self.assertIsNone(info["app_detail"])     # no incinfo/dbcheck on a stopped system

    def test_gather_reports_app_state(self):
        tmp = tempfile.mkdtemp()
        self.assertEqual(localinfo.gather(self._host(app_up=True), "fep1", _cnfg(tmp))["app_state"], "up")
        self.assertEqual(localinfo.gather(self._host(app_up=False), "fep1", _cnfg(tmp))["app_state"], "down")

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


class TestVersionNotes(unittest.TestCase):
    """#10 (2026-11-11 test): after 5.4.0.36 -> 5.4.1.28 Overview showed PATCH
    5.4.0.36-1 next to CORE 5.4.1.28-1 as if current, and a leftover
    netFLEX-CORE-5.4.0.29 record. Flag them; never hide or remove them."""

    QF = ("rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}\\n")

    def host(self, core, patch=(), ipatch=()):
        r = {}
        for pkg, recs in (("netFLEX-CORE", core), ("netFLEX-CORE-PATCH", patch),
                          ("netFLEX-CORE-IPATCH", ipatch)):
            r[self.QF + (pkg,)] = (RunResult(0, "".join(v + "\n" for v in recs), "") if recs
                                   else RunResult(1, "package {} is not installed\n".format(pkg), ""))
        return FakeHost(r)

    def test_clean_box_has_no_notes(self):
        n = localinfo.version_notes(self.host(["5.4.0.29-1"], ["5.4.0.36-1"]))
        self.assertEqual(n, {"core": None, "patch": None, "ipatch": None})

    def test_after_upgrade(self):
        n = localinfo.version_notes(self.host(["5.4.0.29-1", "5.4.1.28-1"], ["5.4.0.36-1"]))
        self.assertEqual(n["core"], "2 netFLEX-CORE records: 5.4.0.29-1, 5.4.1.28-1 "
                                    "(a leftover? see the rpm-matches-load check)")
        self.assertEqual(n["patch"], "left from 5.4.0: not part of the running 5.4.1 release")
        self.assertIsNone(n["ipatch"])

    def test_ipatch_and_no_core(self):
        n = localinfo.version_notes(self.host(["5.4.1.28-1"], [], ["5.4.0.31-1"]))
        self.assertEqual(n["ipatch"], "left from 5.4.0: not part of the running 5.4.1 release")
        self.assertEqual(localinfo.version_notes(self.host([])),
                         {"core": None, "patch": None, "ipatch": None})
