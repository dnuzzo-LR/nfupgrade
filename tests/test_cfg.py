import unittest
from nfupgrader import cfg


class TestParseRender(unittest.TestCase):
    def test_parse_basic_assignments(self):
        text = '# comment\nINCLOGDIR="/usr/cnc/logs"\nNEW_GENERIC=5.5.0\n\nNEW_LOAD="03"\n'
        d = cfg.parse_cfg(text)
        self.assertEqual(d["INCLOGDIR"], "/usr/cnc/logs")
        self.assertEqual(d["NEW_GENERIC"], "5.5.0")
        self.assertEqual(d["NEW_LOAD"], "03")

    def test_parse_ignores_non_assignment_lines(self):
        d = cfg.parse_cfg("echo hi\nFOO=bar\n. /some/file\n")
        self.assertEqual(d, {"FOO": "bar"})

    def test_render_quotes_and_forces_promptforvalidate(self):
        text = cfg.render_cfg({"INCLOGDIR": "/usr/cnc/logs", "NEW_GENERIC": "5.5.0"})
        self.assertIn('INCLOGDIR="/usr/cnc/logs"', text)
        self.assertIn('PROMPTFORVALIDATE=NO', text)

    def test_render_skips_empty_values(self):
        text = cfg.render_cfg({"INCLOGDIR": "/x", "PATCHDEPOTFILE": ""})
        self.assertNotIn("PATCHDEPOTFILE", text)

    def test_derive_generic_load(self):
        self.assertEqual(cfg.derive_generic_load("5.5.0.03"), ("5.5.0", "03"))
        self.assertEqual(cfg.derive_generic_load("5.5.0"), ("5.5.0", ""))

    def test_fields_include_required_and_fixed(self):
        by = {f["name"]: f for f in cfg.FIELDS}
        self.assertTrue(by["NEW_GENERIC"]["required"])
        self.assertTrue(by["DEPOTFILE"]["required"])
        self.assertTrue(by["PROMPTFORVALIDATE"].get("fixed"))


def _ok(extra):
    base = {"INCLOGDIR": "/usr/cnc/logs", "NEW_GENERIC": "5.5.0",
            "NEW_LOAD": "03", "DEPOTFILE": "/depot/netFLEX-CORE-5.5.0.03-1.x86_64.rpm"}
    base.update(extra)
    return base


class TestValidate(unittest.TestCase):
    def test_missing_required_is_error(self):
        issues = cfg.validate({"NEW_GENERIC": "5.5.0"})  # missing INCLOGDIR, NEW_LOAD, DEPOTFILE
        fields = {i["field"]: i for i in issues if i["level"] == "ERROR"}
        self.assertIn("INCLOGDIR", fields)
        self.assertIn("NEW_LOAD", fields)
        self.assertIn("DEPOTFILE", fields)

    def test_enum_out_of_range_is_error(self):
        issues = cfg.validate(_ok({"INSTALLWEBGUI": "MAYBE"}))
        self.assertTrue(any(i["field"] == "INSTALLWEBGUI" and i["level"] == "ERROR" for i in issues))

    def test_int_non_numeric_is_error(self):
        issues = cfg.validate(_ok({"STAGENICEVAL": "high"}))
        self.assertTrue(any(i["field"] == "STAGENICEVAL" and i["level"] == "ERROR" for i in issues))

    def test_negative_int_ok(self):
        issues = cfg.validate(_ok({"UPGRADENICEVAL": "-39"}))
        self.assertFalse(any(i["field"] == "UPGRADENICEVAL" for i in issues))

    def test_clean_config_no_errors(self):
        self.assertEqual([i for i in cfg.validate(_ok({})) if i["level"] == "ERROR"], [])


from nfupgrader.host import FakeHost, RunResult


class TestDepots(unittest.TestCase):
    def test_list_depots_parses_names(self):
        out = ("/depot/netFLEX-CORE-5.5.0.03-1.rhel8.x86_64.rpm\n"
               "/depot/netFLEX-CORE-5.5.0.04-1.rhel8.x86_64.rpm\n")
        h = FakeHost({("find", "/depot", "-maxdepth", "1", "-name", "netFLEX-CORE*.rpm"):
                      RunResult(0, out, "")})
        depots = cfg.list_depots(h, ["/depot"])
        self.assertEqual(len(depots), 2)
        self.assertEqual(depots[0]["generic"], "5.5.0")
        self.assertEqual(depots[0]["load"], "03")
        self.assertEqual(depots[0]["path"], "/depot/netFLEX-CORE-5.5.0.03-1.rhel8.x86_64.rpm")

    def test_list_depots_excludes_patch_files(self):
        out = ("/depot/netFLEX-CORE-PATCH-5.4.1.32-1.rhel8.i686.rpm\n"
               "/depot/netFLEX-CORE-5.4.1.28-1.rhel8.i686.rpm\n")
        h = FakeHost({("find", "/depot", "-maxdepth", "1", "-name", "netFLEX-CORE*.rpm"):
                      RunResult(0, out, "")})
        depots = cfg.list_depots(h, ["/depot"])
        self.assertEqual([d["name"] for d in depots],
                         ["netFLEX-CORE-5.4.1.28-1.rhel8.i686.rpm"])

    def test_list_depots_empty_dir(self):
        h = FakeHost({("find", "/none", "-maxdepth", "1", "-name", "netFLEX-CORE*.rpm"):
                      RunResult(1, "", "")})
        self.assertEqual(cfg.list_depots(h, ["/none"]), [])


class TestInstallMode(unittest.TestCase):
    BASE = {"INCLOGDIR": "/l", "NEW_GENERIC": "5.3.0", "NEW_LOAD": "02", "DEPOTFILE": "/d/x.rpm"}

    def test_fields_for(self):
        inst = [f["name"] for f in cfg.fields_for("install")]
        self.assertIn("BOXTYPE", inst); self.assertIn("INSTALLWEBGUI", inst)
        self.assertNotIn("UNLINKDIR", inst); self.assertNotIn("RESTARTINC", inst)
        self.assertNotIn("BOXTYPE", [f["name"] for f in cfg.fields_for("upgrade")])

    def test_single_ok(self):
        self.assertEqual(cfg.validate(dict(self.BASE, BOXTYPE="SINGLE"), "install", "h1"), [])

    def test_upgrade_validation_ignores_install_fields(self):
        self.assertEqual([i for i in cfg.validate(self.BASE) if i["field"] == "BOXTYPE"], [])

    def test_install_rejects_bad_mate(self):
        for mate in ("", "h1", "b 1", " "):
            issues = cfg.validate(dict(self.BASE, BOXTYPE="FEP", MATEHOST=mate, MASTERHOST="h1"), "install", "h1")
            self.assertIn("MATEHOST", [i["field"] for i in issues if i["level"] == "ERROR"], mate)

    def test_master_must_be_one_of_pair(self):
        issues = cfg.validate(dict(self.BASE, BOXTYPE="BEP", MATEHOST="f1", MASTERHOST="x"), "install", "h1")
        self.assertIn("MASTERHOST", [i["field"] for i in issues])
        self.assertEqual(cfg.validate(dict(self.BASE, BOXTYPE="BEP", MATEHOST="f1", MASTERHOST="f1"), "install", "h1"), [])

    def test_render_includes_install_keys(self):
        out = cfg.render_cfg(dict(self.BASE, BOXTYPE="FEP", MATEHOST="b1", MASTERHOST="h1", CNCCNFG="/l/c"))
        for k in ('BOXTYPE="FEP"', 'MATEHOST="b1"', 'MASTERHOST="h1"', 'CNCCNFG="/l/c"'):
            self.assertIn(k, out)


if __name__ == "__main__":
    unittest.main()
