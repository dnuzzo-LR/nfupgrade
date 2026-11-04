import json
import unittest

from nfupgrader import license as L
from nfupgrader.host import FakeHost, RunResult

LIC = {"NumNE": 100, "NumUsers": 10, "Trial": False, "Text": "x",
       "MaintExpireDate": "2026-12-31 23:59:59",
       "EnabledNeTypes": [{"type": 5, "name": "A", "group": 0, "count": 0}]}

NE_ALL = """
All Network Element Types:
====================================================================================
  Type |                           Name |     Status |  Group |    Count
-------|--------------------------------|------------|--------|----------
     1 | ALPHA                          |    ENABLED |      0 |        0
     5 | (unnamed)                      |   DISABLED |      3 |       12
   254 | BETA 2 | x                     |   DISABLED |      0 |        0
====================================================================================
"""


def _fh(**kw):
    r = {}
    r[("test", "-x", L.LICE)] = RunResult(kw.get("x", 0), "", "")
    r[("test", "-f", "/p")] = RunResult(kw.get("f", 0), "", "")
    if "j" in kw:
        r[(L.LICE, "-j", "/p")] = kw["j"]
    return FakeHost(r)


class TestLicense(unittest.TestCase):
    def test_read_states(self):
        self.assertEqual(L.read_license(_fh(x=1), "/p")["state"], "unavailable")
        self.assertEqual(L.read_license(_fh(f=1), "/p")["state"], "missing")
        old = RunResult(1, "", "This license is from an earlier version of netFLEX")
        self.assertEqual(L.read_license(_fh(j=old), "/p")["state"], "old_format")
        bad = RunResult(2, "", "boom")
        r = L.read_license(_fh(j=bad), "/p")
        self.assertEqual((r["state"], r["error"]), ("unreadable", "boom"))
        r = L.read_license(_fh(j=RunResult(0, json.dumps(LIC), "")), "/p")
        self.assertEqual((r["state"], r["license"]["NumNE"]), ("ok", 100))
        r = L.read_license(_fh(j=RunResult(0, "not json", "")), "/p")
        self.assertEqual(r["state"], "unreadable")

    def test_parse_ne_all(self):
        rows = L.parse_ne_all(NE_ALL)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {"type": 1, "name": "ALPHA", "enabled": True,
                                   "group": 0, "count": 0})
        self.assertEqual(rows[1]["name"], "(unnamed)")
        self.assertEqual((rows[1]["group"], rows[1]["count"], rows[1]["enabled"]),
                         (3, 12, False))
        self.assertEqual(rows[2]["type"], 254)

    def test_expiry_of(self):
        self.assertEqual(L.expiry_of(LIC), "12/31/2026")
        self.assertEqual(L.expiry_of({"MaintExpireDate": "junk"}), "")
        self.assertEqual(L.expiry_of({}), "")

    def test_enabled_types(self):
        self.assertEqual(L.enabled_types(LIC), {5})

    def _fields(self, errs):
        return [e["field"] for e in errs]

    def test_validate_rejects_control_chars(self):
        errs = L.validate({"Text": "a\nsave", "Version": "x\ty"}, "", None)
        self.assertEqual(sorted(self._fields(errs)), ["Text", "Version"])
        self.assertTrue(L.validate({"Text": "a\x7f"}, "", None))
        self.assertEqual(L.validate({"Text": "fine"}, "", None), [])

    def test_validate_expiry(self):
        for bad in ("2027-12-31", "13/45/2027", "02/30/2027", "12/31/1969"):
            self.assertEqual(self._fields(L.validate({}, bad, None)), ["expiry"], bad)
        for ok in ("12/31/2027", "1/5/2027", ""):
            self.assertEqual(L.validate({}, ok, None), [], ok)

    def test_validate_ranges(self):
        for v in (-1, "5x", True, 1.5):
            self.assertEqual(self._fields(L.validate({"NumNE": v}, "", None)),
                             ["NumNE"], repr(v))
        self.assertEqual(self._fields(L.validate({"Version": "x" * 21}, "", None)),
                         ["Version"])
        self.assertEqual(L.validate({"NumNE": 0, "NumUsers": "7"}, "", None), [])
        self.assertTrue(L.validate({"NumWeightedNE": -0.5}, "", None))
        self.assertEqual(L.validate({"NumWeightedNE": 2.5}, "", None), [])
        self.assertTrue(L.validate({"Trial": "true"}, "", None))
        self.assertTrue(L.validate({"Trial": 1}, "", None))
        self.assertEqual(L.validate({"Trial": True}, "", None), [])
        self.assertTrue(L.validate({}, "", [0]))
        self.assertTrue(L.validate({}, "", [256]))
        self.assertTrue(L.validate({}, "", ["5"]))
        self.assertEqual(L.validate({}, "", [1, 255]), [])

    def test_diff_and_script(self):
        d = L.diff(LIC, {"NumNE": 500, "NumUsers": 10, "Trial": True}, "", {5, 7})
        self.assertEqual(d["set_lines"], ["set numne 500", "set trial yes"])
        self.assertEqual(d["ne_toggle"], [7])
        self.assertEqual([c["key"] for c in d["changes"]], ["NumNE", "Trial"])
        self.assertEqual(d["changes"][0]["before"], 100)
        self.assertEqual(d["changes"][0]["after"], 500)
        self.assertEqual(L.build_script("pw", d),
                         "pw\nset numne 500\nset trial yes\nne 7\nyes\nsave\nquit\n")

    def test_diff_ne_none_unchanged(self):
        self.assertEqual(L.diff(LIC, {}, "", None)["ne_toggle"], [])

    def test_diff_disable_type(self):
        self.assertEqual(L.diff(LIC, {}, "", set())["ne_toggle"], [5])

    def test_diff_float_and_text(self):
        d = L.diff(dict(LIC, NumWeightedNE=1.0), {"NumWeightedNE": 2.5, "Text": "y"},
                   "", None)
        self.assertEqual(d["set_lines"], ["set numweightedne 2.5", "set text y"])

    def test_diff_group_warning(self):
        table = L.parse_ne_all(NE_ALL)
        d = L.diff(LIC, {}, "", {5, 1, 254}, table)
        self.assertEqual(d["ne_toggle"], [1, 254])
        self.assertEqual(d["warnings"], [])
        d = L.diff({"EnabledNeTypes": []}, {}, "", {5}, table)
        self.assertEqual(d["warnings"], [
            "NE type 5 ((unnamed)) is in group 3; that group's limit, if any, "
            "still applies"])

    def test_diff_expiry(self):
        self.assertIsNone(L.diff(LIC, {}, "", None)["expiry"])
        self.assertIsNone(L.diff(LIC, {}, "12/31/2026", None)["expiry"])
        self.assertIsNone(L.diff(LIC, {}, "12/31/2026", None)["expiry"])
        self.assertEqual(L.diff(LIC, {}, "1/5/2027", None)["expiry"], "01/05/2027")


if __name__ == "__main__":
    unittest.main()
