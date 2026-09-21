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


if __name__ == "__main__":
    unittest.main()
