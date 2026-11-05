import unittest

from nfupgrader import patches

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


if __name__ == "__main__":
    unittest.main()
