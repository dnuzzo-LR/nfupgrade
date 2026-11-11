import unittest

from nfupgrader import patches
from nfupgrader.host import FakeHost, RunResult

CORE = {"core": "5.4.1.28", "core_arch": "i686", "patch": None}


def f(name, pkg, ver, arch="i686", loads=None):
    return {"path": "/d/" + name, "name": name, "pkg": pkg, "version": ver,
            "arch": arch, "reboot_loads": loads or []}


def p(ver, arch="i686", loads=None):
    return f("netFLEX-CORE-PATCH-" + str(ver) + ".rpm", "netFLEX-CORE-PATCH",
             ver, arch, loads)


def by_ver(rows):
    return [(r["version"], r["status"]) for r in rows]


class TestHelpers(unittest.TestCase):
    def test_split_version(self):
        self.assertIsNone(patches.split_version("5.4.1"))
        self.assertIsNone(patches.split_version("5.4.1.x"))
        self.assertEqual(patches.split_version("5.4.1.32"), ("5.4.1", 32))
        self.assertEqual(patches.split_version("5.5.0.07"), ("5.5.0", 7))

    def test_parse_reboot_loads(self):
        self.assertEqual(patches.parse_reboot_loads('x\nREBOOT_LOADS="29 31"\n'), [29, 31])
        self.assertEqual(patches.parse_reboot_loads('REBOOT_LOADS=""'), [])
        self.assertEqual(patches.parse_reboot_loads("nothing"), [])

    def test_reboot_required(self):
        self.assertTrue(patches.reboot_required([31], 30, 31))
        self.assertFalse(patches.reboot_required([29], 30, 31))
        self.assertTrue(patches.reboot_required([40], 30, 31))

    def test_current_load(self):
        self.assertEqual(patches.current_load(CORE), 28)
        self.assertEqual(patches.current_load(dict(CORE, patch="5.4.1.30")), 30)
        self.assertEqual(patches.current_load(dict(CORE, patch="5.3.0.40")), 28)
        self.assertIsNone(patches.current_load({"core": None, "core_arch": None, "patch": None}))


class TestEvaluate(unittest.TestCase):
    def ev(self, files, installed=CORE, host_block=None, app_up=False):
        return patches.evaluate(installed, files, host_block, app_up)

    def test_none_installed_lowest_above_core_is_next(self):
        rows = self.ev([p("5.4.1.30"), p("5.4.1.29"), p("5.4.1.31")])
        self.assertEqual(by_ver(rows), [("5.4.1.30", "out_of_sequence"),
                                        ("5.4.1.29", "installable"),
                                        ("5.4.1.31", "out_of_sequence")])
        self.assertEqual(rows[0]["reason"], "install 5.4.1.29 first")
        self.assertEqual(rows[2]["reason"], "install 5.4.1.29 first")

    def test_core_load_or_below_is_installed(self):
        rows = self.ev([p("5.4.1.28")])
        self.assertEqual(rows[0]["status"], "installed")

    def test_patch_installed_strict_next(self):
        inst = dict(CORE, patch="5.4.1.30")
        rows = self.ev([p("5.4.1.30"), p("5.4.1.31"), p("5.4.1.32")], inst)
        self.assertEqual([r["status"] for r in rows],
                         ["installed", "installable", "out_of_sequence"])
        self.assertEqual(rows[2]["reason"], "install 5.4.1.31 first")

    def test_gap_nothing_installable(self):
        inst = dict(CORE, patch="5.4.1.30")
        rows = self.ev([p("5.4.1.32")], inst)
        self.assertEqual(rows[0]["status"], "out_of_sequence")
        self.assertFalse([r for r in rows if r["status"] == "installable"])

    def test_wrong_release(self):
        rows = self.ev([p("5.5.0.02")])
        self.assertEqual(rows[0]["status"], "wrong_release")
        self.assertIn("5.4.1", rows[0]["reason"])

    def test_wrong_arch(self):
        rows = self.ev([p("5.4.1.29", arch="x86_64")])
        self.assertEqual(rows[0]["status"], "wrong_arch")

    def test_unsupported_names(self):
        rows = self.ev([f("a.rpm", n, "5.4.1.29") for n in
                        ("netFLEX-CORE-IPATCH", "INC-CORE-PATCH", "INC-ATTEXT-PATCH")])
        self.assertEqual([r["status"] for r in rows], ["unsupported"] * 3)

    def test_unreadable(self):
        rows = self.ev([f("bad.rpm", None, None, None), p("5.4.1.29")])
        self.assertEqual(rows[0]["status"], "unreadable")
        self.assertEqual(rows[1]["status"], "installable")

    def test_leading_zero_loads(self):
        core = {"core": "5.5.0.07", "core_arch": "i686", "patch": None}
        rows = self.ev([p("5.5.0.08"), p("5.5.0.9")], core)
        self.assertEqual([r["status"] for r in rows], ["installable", "out_of_sequence"])

    def test_duplicate_version_only_first_installable(self):
        a = f("a.rpm", "netFLEX-CORE-PATCH", "5.4.1.29")
        b = f("b.rpm", "netFLEX-CORE-PATCH", "5.4.1.29")
        rows = self.ev([a, b])
        self.assertEqual([r["status"] for r in rows], ["installable", "out_of_sequence"])
        self.assertEqual(rows[1]["reason"], "duplicate of a.rpm")

    def test_host_block_blocks_all(self):
        hb = "a staged upgrade is present (/usr/cnc_stage)"
        rows = self.ev([p("5.4.1.28"), p("5.4.1.29"), p("5.4.1.30")], host_block=hb)
        self.assertEqual([r["status"] for r in rows],
                         ["installed", "blocked", "out_of_sequence"])
        self.assertEqual(rows[1]["reason"], hb)

    def test_needs_stop(self):
        rows = self.ev([p("5.4.1.29", loads=[29])], app_up=True)
        self.assertTrue(rows[0]["needs_stop"])
        rows = self.ev([p("5.4.1.29", loads=[29])], app_up=False)
        self.assertFalse(rows[0]["needs_stop"])
        rows = self.ev([p("5.4.1.29")], app_up=True)
        self.assertFalse(rows[0]["needs_stop"])

    def test_no_core(self):
        inst = {"core": None, "core_arch": None, "patch": None}
        rows = self.ev([p("5.4.1.29"), f("x.rpm", "INC-CORE-PATCH", "5.4.1.29"),
                        f("bad.rpm", None, None, None)], inst)
        self.assertEqual([r["status"] for r in rows], ["blocked", "unsupported", "unreadable"])
        self.assertEqual(rows[0]["reason"], "netFLEX-CORE is not installed")



def _fake(core="5.4.1.28 i686\n", patch="", ipatch=False, files=None, lock=False,
          stage=False, link=False, up=False, stale=False, loads="", dirs=("/d",)):
    r = {}
    ok = RunResult(0, "", "")
    for d in dirs:
        names = (files or {}).get(d)
        if names is None:
            continue
        r[("find", d, "-maxdepth", "1", "-name", "*PATCH*.rpm")] = RunResult(
            0, "".join(d + "/" + n + "\n" for n in names), "")
        for n in names:
            ver = n.split("PATCH-")[1].split("-")[0]
            r[("rpm", "-qp", "--qf", "%{NAME} %{VERSION} %{ARCH}\\n", d + "/" + n)] = \
                RunResult(0, "netFLEX-CORE-PATCH " + ver + " i686\n", "")
            r[("rpm", "-qp", "--scripts", d + "/" + n)] = RunResult(0, loads, "")
    r[("rpm", "-q", "--qf", "%{VERSION} %{ARCH}\\n", "netFLEX-CORE")] = (
        RunResult(0, core, "") if core else RunResult(1, "", ""))
    r[("rpm", "-q", "--qf", "%{VERSION}\\n", "netFLEX-CORE-PATCH")] = (
        RunResult(0, patch, "") if patch else RunResult(1, "", ""))
    if ipatch:
        r[("rpm", "-q", "netFLEX-CORE-IPATCH")] = ok
    if lock:
        r[("test", "-f", "/usr/cnc/.UPGRADE_LOCK")] = ok
    if stage:
        r[("test", "-e", "/usr/cnc_stage")] = ok
    if link:
        r[("test", "-L", "/usr/cnc_stage")] = ok
    if up:
        r[("test", "-f", "/usr/cnc/.CNC_UP")] = ok
        r[("ps", "-e", "-o", "user=,comm=")] = RunResult(0, "cmm dacs\n", "")
    if stale:
        r[("test", "-f", "/usr/cnc/.CNC_UP")] = ok
        r[("ps", "-e", "-o", "user=,comm=")] = RunResult(0, "cmm ksh\n", "")
    return FakeHost(r)


N29 = "netFLEX-CORE-PATCH-5.4.1.29-1.rhel8.i686.rpm"
N30 = "netFLEX-CORE-PATCH-5.4.1.30-1.rhel8.i686.rpm"
N31 = "netFLEX-CORE-PATCH-5.4.1.31-1.rhel8.i686.rpm"


WEBINFO = ("/usr/cnc/web/bin/webinfo", "-j")


def webinfo_out(release, build):
    """webinfo -j as netFLEX prints it (gui/src/web/webinfo.c)."""
    return '{ "release" : "%s", "build" : "%s"}\n' % (release, build)


class TestWebStatus(unittest.TestCase):
    """#1 (A): the web GUI's build must match the installed CORE/PATCH load."""

    def test_parse_webinfo(self):
        self.assertEqual(patches.parse_webinfo(webinfo_out("5.4.0", "36")), ("5.4.0", 36))
        self.assertEqual(patches.parse_webinfo(webinfo_out("5.4.0", "036")), ("5.4.0", 36))
        self.assertIsNone(patches.parse_webinfo(""))
        self.assertIsNone(patches.parse_webinfo("not json"))
        self.assertIsNone(patches.parse_webinfo(webinfo_out("5.4.0", "x")))

    def st(self, out, rc=0, core="5.4.0.29", patch=None):
        h = FakeHost({WEBINFO: RunResult(rc, out, "")})
        inst = {"core": core, "core_arch": "x86_64", "patch": patch}
        return patches.web_status(h, inst)

    def test_web_behind_patch(self):
        self.assertEqual(self.st(webinfo_out("5.4.0", "35"), patch="5.4.0.36"),
                         {"web": "5.4.0.35", "expected": "5.4.0.36", "matches": False})

    def test_web_matches_patch_or_core(self):
        self.assertTrue(self.st(webinfo_out("5.4.0", "36"), patch="5.4.0.36")["matches"])
        self.assertTrue(self.st(webinfo_out("5.4.0", "29"))["matches"])

    def test_other_generic_does_not_match(self):
        self.assertFalse(self.st(webinfo_out("5.4.1", "29"))["matches"])

    def test_unknown_when_webinfo_missing_or_core_absent(self):
        self.assertIsNone(self.st("", rc=127))
        r = self.st(webinfo_out("5.4.0", "29"), core=None)
        self.assertEqual((r["web"], r["expected"], r["matches"]), ("5.4.0.29", None, None))


class TestGather(unittest.TestCase):
    def test_gather_reports_web_status(self):
        h = _fake(files={"/d": [N29]})
        h.responses[WEBINFO] = RunResult(0, webinfo_out("5.4.1", "27"), "")
        g = patches.gather(h, ["/d"])
        self.assertEqual(g["web"], {"web": "5.4.1.27", "expected": "5.4.1.28", "matches": False})

    def test_gather_lists_and_evaluates(self):
        h = _fake(files={"/d": [N30, N29]})
        g = patches.gather(h, ["/d"])
        self.assertEqual([(r["version"], r["status"]) for r in g["patches"]],
                         [("5.4.1.29", "installable"), ("5.4.1.30", "out_of_sequence")])
        self.assertEqual(g["installed"]["current_load"], 28)
        self.assertIsNone(g["host_block"])
        self.assertFalse(g["app_up"])
        scripts = [c for c in h.calls if c[:3] == ["rpm", "-qp", "--scripts"]]
        self.assertEqual(scripts, [["rpm", "-qp", "--scripts", "/d/" + N29]])

    def test_multiple_installed_patch_versions_use_highest(self):
        h = _fake(patch="5.4.1.29\n5.4.1.30\n", files={"/d": [N31]})
        g = patches.gather(h, ["/d"])
        self.assertEqual(g["installed"]["patch"], "5.4.1.30")
        self.assertEqual(g["patches"][0]["status"], "installable")

    def test_stale_cnc_up_does_not_need_stop(self):
        h = _fake(files={"/d": [N29]}, stale=True, loads='REBOOT_LOADS="29"\n')
        g = patches.gather(h, ["/d"])
        self.assertFalse(g["app_up"])
        self.assertFalse(g["patches"][0]["needs_stop"])

    def test_reboot_loads_and_app_up(self):
        h = _fake(files={"/d": [N29]}, up=True, loads='REBOOT_LOADS="29"\n')
        g = patches.gather(h, ["/d"])
        self.assertTrue(g["app_up"])
        self.assertEqual(g["patches"][0]["reboot_loads"], [29])
        self.assertTrue(g["patches"][0]["needs_stop"])

    def test_missing_dir_skipped(self):
        h = _fake(files={"/d": [N29]})
        g = patches.gather(h, ["/nope", "/d"])
        self.assertEqual(len(g["patches"]), 1)

    def test_core_missing_blocks(self):
        g = patches.gather(_fake(core="", files={"/d": [N29]}), ["/d"])
        self.assertEqual(g["host_block"], "netFLEX-CORE is not installed")
        self.assertIsNone(g["installed"]["core"])

    def test_ipatch_blocks(self):
        g = patches.gather(_fake(ipatch=True), ["/d"])
        self.assertEqual(g["host_block"], "an incremental patch (netFLEX-CORE-IPATCH) "
                         "is installed \u2014 contact support")

    def test_stage_link_blocks(self):
        want = ("a staged upgrade is present (/usr/cnc_stage) \u2014 "
                "finish or back out the upgrade first")
        self.assertEqual(patches.gather(_fake(link=True), ["/d"])["host_block"], want)
        self.assertEqual(patches.gather(_fake(stage=True), ["/d"])["host_block"], want)

    def test_upgrade_lock_blocks(self):
        g = patches.gather(_fake(lock=True), ["/d"])
        self.assertEqual(g["host_block"], "an upgrade is in progress (/usr/cnc/.UPGRADE_LOCK)")

    def test_highest_installed_patch_only_on_core_generic(self):
        # A leftover PATCH of another generic (5.4.0.40) has the higher load
        # but must not hide the 5.4.1 patch: current is 29, not CORE's 28.
        h = _fake(patch="5.4.0.40\n5.4.1.29\n", files={"/d": [N29, N30]})
        g = patches.gather(h, ["/d"])
        self.assertEqual(g["installed"]["patch"], "5.4.1.29")
        self.assertEqual(g["installed"]["current_load"], 29)
        self.assertEqual([(r["version"], r["status"]) for r in g["patches"]],
                         [("5.4.1.29", "installed"), ("5.4.1.30", "installable")])


WB = "/o/nf_webbuild"


def _web(h, d, tars, builds=None, major="8.10", mtime="100"):
    """Add a RHEL VERSION_ID, LINUXWEB* tars in dir d, and their nf_webbuild
    answers ({name: (release, build)}; absent = unreadable) to FakeHost h."""
    builds = builds or {}
    h.responses[("cat", "/etc/os-release")] = RunResult(
        0, 'NAME="Red Hat Enterprise Linux"\nVERSION_ID="%s"\n' % major, "")
    h.responses[("find", d, "-maxdepth", "1", "-name", "LINUXWEB*.tar")] = RunResult(
        0, "".join(d + "/" + t + "\n" for t in tars), "")
    for t in tars:
        path = d + "/" + t
        h.responses[("stat", "-c", "%s %Y", path)] = RunResult(0, "5000 " + mtime + "\n", "")
        if t in builds:
            h.responses[(WB, path)] = RunResult(0, webinfo_out(*builds[t]), "")
        else:
            h.responses[(WB, path)] = RunResult(1, "", "nf_webbuild: webinfo did not run")
    return h


class TestWebTar(unittest.TestCase):
    """#1 (B): the web GUI tar beside the eligible patch RPM."""

    def setUp(self):
        patches._TAR_BUILDS.clear()

    def test_rhel_major(self):
        h = FakeHost({("cat", "/etc/os-release"): RunResult(0, 'VERSION_ID="8.10"\n', "")})
        self.assertEqual(patches.rhel_major(h), "8")
        h = FakeHost({("cat", "/etc/os-release"): RunResult(0, "VERSION_ID=9\n", "")})
        self.assertEqual(patches.rhel_major(h), "9")
        h = FakeHost({("cat", "/etc/os-release"): RunResult(0, 'NAME="x"\n', "")})
        self.assertIsNone(patches.rhel_major(h))
        self.assertIsNone(patches.rhel_major(FakeHost()))

    def test_candidates_filter(self):
        h = _web(FakeHost(), "/d", ["LINUXWEB64.rhel8.tar", "LINUXWEB64.36.rhel8.tar",
                                    "LINUXWEB64.rhel9.tar", "LINUXWEB64ATT.rhel8.tar",
                                    "LINUXWEB64.tar"])
        self.assertEqual(patches.web_tar_candidates(h, "/d", "8"),
                         ["/d/LINUXWEB64.36.rhel8.tar", "/d/LINUXWEB64.rhel8.tar"])
        self.assertEqual(patches.web_tar_candidates(h, "/nope", "8"), [])

    def test_pick_prefers_numbered_match(self):
        tars = [("/d/LINUXWEB64.rhel8.tar", ("5.4.0", 36)),
                ("/d/LINUXWEB64.36.rhel8.tar", ("5.4.0", 36))]
        self.assertEqual(patches.pick_web_tar("5.4.0.36", tars),
                         ("/d/LINUXWEB64.36.rhel8.tar", None))
        self.assertEqual(patches.pick_web_tar("5.4.0.036", tars[:1]),
                         ("/d/LINUXWEB64.rhel8.tar", None))

    def test_pick_mismatch_note(self):
        self.assertEqual(
            patches.pick_web_tar("5.4.0.36", [("/d/LINUXWEB64.rhel8.tar", ("5.4.0", 37))]),
            (None, "no web GUI tar for 5.4.0.36 found (found: 5.4.0.37)"))
        # another generic with the same load is not a match
        self.assertEqual(
            patches.pick_web_tar("5.4.0.36", [("/d/a.rhel8.tar", ("5.4.1", 36)),
                                              ("/d/b.rhel8.tar", ("5.4.1", 36))]),
            (None, "no web GUI tar for 5.4.0.36 found (found: 5.4.1.36)"))

    def test_pick_none_found(self):
        self.assertEqual(patches.pick_web_tar("5.4.0.36", []),
                         (None, "no web GUI tar for 5.4.0.36 found"))

    def test_pick_unreadable(self):
        self.assertEqual(
            patches.pick_web_tar("5.4.0.36", [("/d/LINUXWEB64.rhel8.tar", None)]),
            (None, "could not read the web GUI build from LINUXWEB64.rhel8.tar"))
        # an unreadable tar beside a mismatched one: say what was found
        self.assertEqual(
            patches.pick_web_tar("5.4.0.36", [("/d/a.rhel8.tar", None),
                                              ("/d/b.rhel8.tar", ("5.4.0", 35))]),
            (None, "no web GUI tar for 5.4.0.36 found (found: 5.4.0.35)"))

    def test_read_tar_build_cached(self):
        path = "/d/LINUXWEB64.rhel8.tar"
        h = _web(FakeHost(), "/d", ["LINUXWEB64.rhel8.tar"],
                 {"LINUXWEB64.rhel8.tar": ("5.4.0", "36")})
        self.assertEqual(patches.read_tar_build(h, path, WB), ("5.4.0", 36))
        self.assertEqual(patches.read_tar_build(h, path, WB), ("5.4.0", 36))
        self.assertEqual(sum(1 for c in h.calls if c[0] == WB), 1)
        h.responses[("stat", "-c", "%s %Y", path)] = RunResult(0, "5000 200\n", "")
        patches.read_tar_build(h, path, WB)
        self.assertEqual(sum(1 for c in h.calls if c[0] == WB), 2)

    def test_read_tar_build_unreadable(self):
        h = _web(FakeHost(), "/d", ["LINUXWEB64.rhel8.tar"])
        self.assertIsNone(patches.read_tar_build(h, "/d/LINUXWEB64.rhel8.tar", WB))
        # gone: no stat
        self.assertIsNone(patches.read_tar_build(FakeHost(), "/d/x.tar", WB))

    def test_gather_installable_row_gets_web_tar(self):
        h = _fake(files={"/d": [N29, N30]})
        _web(h, "/d", ["LINUXWEB64.29.rhel8.tar"], {"LINUXWEB64.29.rhel8.tar": ("5.4.1", "29")})
        g = patches.gather(h, ["/d"], webbuild=WB)
        row, other = g["patches"]
        self.assertEqual(row["web_tar"], "/d/LINUXWEB64.29.rhel8.tar")
        self.assertIsNone(row["web_tar_note"])
        self.assertNotIn("web_tar", other)

    def test_gather_without_webbuild_has_no_web_keys(self):
        h = _fake(files={"/d": [N29]})
        _web(h, "/d", ["LINUXWEB64.29.rhel8.tar"], {"LINUXWEB64.29.rhel8.tar": ("5.4.1", "29")})
        row = patches.gather(h, ["/d"])["patches"][0]
        self.assertNotIn("web_tar", row)
        self.assertFalse([c for c in h.calls if c[0] == WB])

    def test_gather_only_rpm_directory(self):
        h = _fake(files={"/d": [N29], "/e": []}, dirs=("/d", "/e"))
        _web(h, "/d", [])
        _web(h, "/e", ["LINUXWEB64.29.rhel8.tar"], {"LINUXWEB64.29.rhel8.tar": ("5.4.1", "29")})
        row = patches.gather(h, ["/d", "/e"], webbuild=WB)["patches"][0]
        self.assertIsNone(row["web_tar"])
        self.assertEqual(row["web_tar_note"], "no web GUI tar for 5.4.1.29 found")

    def test_gather_other_rhel_tar_ignored(self):
        h = _fake(files={"/d": [N29]})
        _web(h, "/d", ["LINUXWEB64.rhel9.tar"], {"LINUXWEB64.rhel9.tar": ("5.4.1", "29")})
        row = patches.gather(h, ["/d"], webbuild=WB)["patches"][0]
        self.assertIsNone(row["web_tar"])
        self.assertFalse([c for c in h.calls if c[0] == WB])


if __name__ == "__main__":
    unittest.main()
