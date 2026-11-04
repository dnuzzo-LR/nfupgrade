import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

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
        self.assertTrue(L.validate({}, "", [264]))
        self.assertTrue(L.validate({}, "", ["5"]))
        self.assertEqual(L.validate({}, "", [1, 255]), [])

    def test_validate_ne_types_up_to_263(self):
        # MAX_NE_TYPES is 264 (netflex common/include/ne_defs.h): 1..263 are real.
        self.assertEqual(L.NE_TYPE_MAX, 263)
        self.assertEqual(L.validate({}, "", [256, 260, 263]), [])
        errs = L.validate({}, "", [264])
        self.assertEqual(self._fields(errs), ["ne_on"])
        self.assertIn("263", errs[0]["message"])

    def test_diff_type_260_untouched(self):
        lic = dict(LIC, EnabledNeTypes=[{"type": 5}, {"type": 260}])
        table = [{"type": n, "name": "", "enabled": False, "group": 0, "count": 0}
                 for n in (1, 5, 260)]
        d = L.diff(lic, {"NumNE": 7}, "", [5, 260], table)
        self.assertEqual((d["ne_toggle"], d["set_lines"]), ([], ["set numne 7"]))

    def test_diff_never_toggles_unseen_types(self):
        # lice -j can report type 0; the checklist starts at 1.
        lic = dict(LIC, EnabledNeTypes=[{"type": 0}, {"type": 5}, {"type": 9}])
        self.assertEqual(L.diff(lic, {}, "", [5, 9])["ne_toggle"], [])
        self.assertEqual(L.diff(lic, {}, "", [5])["ne_toggle"], [9])
        # With an unlock table, only types in it may be toggled.
        table = L.parse_ne_all(NE_ALL)  # types 1, 5, 254
        d = L.diff(lic, {}, "", [5, 1], table)
        self.assertEqual((d["ne_toggle"], d["ne_enable"]), ([1], [1]))
        self.assertEqual(L.diff(lic, {}, "", [5, 7], table)["ne_toggle"], [])

    def test_validate_text_127_bytes(self):
        # license.h: char text[LIC_TEXT_SIZE] (128); lice strncpy's 128 -> no NUL.
        self.assertEqual(L.validate({"Text": "x" * 127}, "", None), [])
        self.assertEqual(self._fields(L.validate({"Text": "x" * 128}, "", None)),
                         ["Text"])
        self.assertEqual(L.validate({"Version": "x" * 20}, "", None), [])

    def test_validate_rejects_non_ascii_text(self):
        for k in ("Text", "Version"):
            errs = L.validate({k: "caf\u00e9"}, "", None)
            self.assertEqual(self._fields(errs), [k])
            self.assertIn("ASCII", errs[0]["message"])

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

    def test_validate_rejects_empty_and_edge_spaces(self):
        for k in ("Text", "Version"):
            for v in ("", "   ", " a", "a ", "\u00a0"):
                self.assertEqual(self._fields(L.validate({k: v}, "", None)), [k], repr(v))
        self.assertEqual(L.validate({"Text": "a b"}, "", None), [])

    def test_validate_int_cap(self):
        self.assertEqual(L.validate({"NumNE": 2147483647}, "", None), [])
        self.assertTrue(L.validate({"NumNE": 2147483648}, "", None))
        self.assertTrue(L.validate({"NumNE": "2147483648"}, "", None))
        self.assertTrue(L.validate({"NumWeightedNE": 2147483648.0}, "", None))

    def test_float_two_decimals(self):
        self.assertTrue(L.validate({"NumWeightedNE": 1.234}, "", None))
        self.assertEqual(L.validate({"NumWeightedNE": 1234.5}, "", None), [])
        d = L.diff(dict(LIC, NumWeightedNE=1234.50), {"NumWeightedNE": 1234.5}, "", None)
        self.assertEqual(d["changes"], [])
        d = L.diff(dict(LIC, NumWeightedNE=1.0), {"NumWeightedNE": 2.5}, "", None)
        self.assertEqual(d["set_lines"], ["set numweightedne 2.50"])

    def test_build_script_rejects_newline_password_and_bad_toggle(self):
        d = {"set_lines": [], "ne_toggle": [], "expiry": None}
        for pw in ("a\nb", "a\rb"):
            with self.assertRaises(ValueError):
                L.build_script(pw, d)
        for bad in ("5", True, 5.0):
            with self.assertRaises(ValueError):
                L.build_script("pw", dict(d, ne_toggle=[bad]))

    def test_validate_max_len_bytes(self):
        self.assertEqual(self._fields(L.validate({"Version": "\u00e9" * 20}, "", None)),
                         ["Version"])
        self.assertEqual(self._fields(L.validate({"Version": "\u00e9" * 10}, "", None)),
                         ["Version"])  # non-ASCII

    def test_diff_disable_type(self):
        self.assertEqual(L.diff(LIC, {}, "", set())["ne_toggle"], [5])

    def test_diff_float_and_text(self):
        d = L.diff(dict(LIC, NumWeightedNE=1.0), {"NumWeightedNE": 2.5, "Text": "y"},
                   "", None)
        self.assertEqual(d["set_lines"], ["set numweightedne 2.50", "set text y"])

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


FIELD_BY_SET = dict((f["set"], f) for f in L.FIELDS)


class FakeLice(object):
    """Emulates lice -i / -j and license -M on a dict "file"."""

    def __init__(self, lic, password="pw", ignore=()):
        self.lic = json.loads(json.dumps(lic))
        self.password = password
        self.ignore = set(ignore)
        self.eof_spins = []  # tools that hit stdin EOF at a password prompt

    def host(self, path="/p"):
        r = {("test", "-x", L.LICE): RunResult(0, "", ""),
             ("test", "-f", path): RunResult(0, "", ""),
             (L.LICE, "-j", path): lambda a, i: RunResult(0, json.dumps(self.lic), ""),
             (L.LICE, "-i", path): self._lice_i}
        for exp in ("01/05/2027", "12/31/2027"):
            r[(L.LICENSE, "-n", path, "-M", exp)] = self._license_m
        return FakeHost(r)

    def _auth(self, lines, tool):
        """Real lice/license loop forever on EOF at the prompt: the service
        would only see a timeout (rc 124), so report that."""
        out, err = "", ""
        for n in range(3):
            out += "Password: "
            if not lines:
                self.eof_spins.append(tool)
                return None, out, "timeout: {} spun on EOF".format(tool)
            if lines.pop(0) == self.password:
                return True, out, err
            err += "Invalid password.\n" if n < 2 else "Too many incorrect attempts.\n"
        return False, out, err

    def _lice_i(self, argv, inp):
        lines = (inp or "").splitlines()
        ok, out, err = self._auth(lines, "lice")
        if ok is None:
            return RunResult(124, "", err)
        if not ok:
            return RunResult(1, out, err)
        out += "Authentication successful.\n"
        work = json.loads(json.dumps(self.lic))
        while lines:
            line = lines.pop(0)
            w = line.split(" ", 2)
            if w[0] == "set":
                f = FIELD_BY_SET[w[1]]
                if w[1] in self.ignore:
                    continue
                v = w[2]
                if f["kind"] == "int":
                    v = int(v)
                elif f["kind"] == "float":
                    v = float(v)
                elif f["kind"] == "bool":
                    v = v == "yes"
                work[f["key"]] = v
            elif w[0] == "ne" and w[1] == "all":
                out += NE_ALL
            elif w[0] == "ne":
                n = int(w[1])
                out += "Toggle this setting? (yes/no): "
                if lines.pop(0) == "yes":
                    ne = [e for e in work["EnabledNeTypes"] if e["type"] != n]
                    if len(ne) == len(work["EnabledNeTypes"]):
                        ne.append({"type": n, "name": "", "group": 0, "count": 0})
                    work["EnabledNeTypes"] = ne
            elif w[0] == "save":
                self.lic = json.loads(json.dumps(work))
                out += "License saved successfully.\n"
            elif w[0] == "quit":
                break
        return RunResult(0, out, err)

    def _license_m(self, argv, inp):
        ok, out, err = self._auth((inp or "").splitlines(), "license")
        if ok is None:
            return RunResult(124, "", err)
        if not ok:
            return RunResult(1, out, err)
        m, d, y = argv[4].split("/")
        self.lic["MaintExpireDate"] = "{}-{}-{} 23:59:59".format(y, m, d)
        return RunResult(0, "Complete.\n", "")


def _argv0s(host):
    return [tuple(c[:2]) for c in host.calls]


class TestLicenseWrite(unittest.TestCase):
    def test_unlock_ok_and_wrong(self):
        fl = FakeLice(LIC)
        h = fl.host()
        r = L.unlock(h, "pw", "/p")
        self.assertTrue(r["ok"])
        self.assertEqual([t["type"] for t in r["ne_types"]], [1, 5, 254])
        self.assertEqual(h.inputs[-1], "pw\nne all\nquit\n")
        r = L.unlock(fl.host(), "nope", "/p")
        self.assertEqual((r["ok"], r["error"], r["ne_types"]),
                         (False, "wrong license password", []))
        h = FakeHost({(L.LICE, "-i", "/p"): RunResult(1, "", "boom\n")})
        self.assertEqual(L.unlock(h, "pw", "/p")["error"], "boom")
        h = FakeHost({})
        for bad in (None, 5, "a\nb", ""):
            r = L.unlock(h, bad, "/p")
            self.assertEqual((r["ok"], r["error"]), (False, "invalid license password"))
        self.assertEqual(h.calls, [])

    def _diff(self, fl, values, expiry="", ne_on=None):
        return L.diff(fl.lic, values, expiry, ne_on)

    def test_apply_happy_path(self):
        fl = FakeLice(LIC)
        h = fl.host()
        d = self._diff(fl, {"NumNE": 500, "NumWeightedNE": 2.5}, "1/5/2027", {5, 7})
        r = L.apply(h, "pw", d, "/p")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["not_applied"], [])
        self.assertEqual(r["applied"], ["NumNE", "NumWeightedNE", "NE type 7", "Expiry"])
        self.assertEqual(r["error"], "")
        self.assertEqual(fl.lic["NumNE"], 500)
        self.assertEqual(L.enabled_types(fl.lic), {5, 7})
        self.assertEqual(L.expiry_of(r["license"]), "01/05/2027")
        i = h.calls.index([L.LICE, "-i", "/p"])
        self.assertTrue(h.inputs[i].startswith("pw\n"))
        m = h.calls.index([L.LICENSE, "-n", "/p", "-M", "01/05/2027"])
        self.assertEqual(h.inputs[m], "pw\npw\npw\n")
        self.assertLess(i, m)
        self.assertEqual(fl.eof_spins, [])

    def test_apply_disable_type_and_expiry_only(self):
        fl = FakeLice(LIC)
        r = L.apply(fl.host(), "pw", self._diff(fl, {}, "", set()), "/p")
        self.assertEqual((r["ok"], r["applied"]), (True, ["NE type 5"]))
        h = fl.host()
        r = L.apply(h, "pw", self._diff(fl, {}, "12/31/2027"), "/p")
        self.assertEqual((r["ok"], r["applied"]), (True, ["Expiry"]))
        # Expiry-only still checks the password with lice first, but never saves.
        i = h.calls.index([L.LICE, "-i", "/p"])
        self.assertNotIn("save", h.inputs[i])

    def test_diff_ne_enable(self):
        d = L.diff(LIC, {}, "", {7})
        self.assertEqual((d["ne_toggle"], d["ne_enable"]), ([5, 7], [7]))
        self.assertEqual(L.diff(LIC, {}, "", None)["ne_enable"], [])

    def test_apply_hand_built_diff_toggle(self):
        fl = FakeLice(LIC)
        d = {"changes": [], "set_lines": [], "ne_toggle": [5, 9], "expiry": None}
        r = L.apply(fl.host(), "pw", d, "/p")
        self.assertEqual((r["ok"], r["applied"]), (True, ["NE type 5", "NE type 9"]))
        self.assertEqual(L.enabled_types(fl.lic), {9})

    def test_apply_readback_failure(self):
        fl = FakeLice(LIC)
        h = fl.host()
        h.responses[(L.LICE, "-j", "/p")] = RunResult(2, "", "gone")
        r = L.apply(h, "pw", self._diff(fl, {"NumNE": 9}), "/p")
        self.assertEqual((r["ok"], r["not_applied"], r["license"]),
                         (False, ["NumNE"], None))
        self.assertIn("gone", r["error"])

    def test_apply_expiry_only_wrong_password(self):
        fl = FakeLice(LIC)
        before = json.loads(json.dumps(fl.lic))
        h = fl.host()
        r = L.apply(h, "bad", self._diff(fl, {}, "1/5/2027"), "/p")
        self.assertEqual((r["ok"], r["error"], r["applied"], r["not_applied"]),
                         (False, "wrong license password", [], ["Expiry"]))
        self.assertNotIn(L.LICENSE, [c[0] for c in h.calls])
        self.assertEqual(fl.eof_spins, [])
        self.assertEqual(fl.lic, before)
        i = h.calls.index([L.LICE, "-i", "/p"])
        self.assertGreaterEqual(h.inputs[i].count("\n"), 3)

    def test_license_m_gets_password_thrice(self):
        fl = FakeLice(LIC)
        h = fl.host()
        r = L.apply(h, "pw", self._diff(fl, {}, "1/5/2027"), "/p")
        self.assertEqual((r["ok"], r["applied"]), (True, ["Expiry"]))
        m = h.calls.index([L.LICENSE, "-n", "/p", "-M", "01/05/2027"])
        self.assertEqual(h.inputs[m], "pw\npw\npw\n")
        self.assertLess(h.calls.index([L.LICE, "-i", "/p"]), m)
        self.assertEqual(fl.eof_spins, [])

    def test_license_m_uses_lic_path(self):
        fl = FakeLice(LIC)
        h = fl.host("/x/lic.ucs")
        r = L.apply(h, "pw", self._diff(fl, {}, "1/5/2027"), "/x/lic.ucs")
        self.assertTrue(r["ok"], r)
        self.assertIn([L.LICENSE, "-n", "/x/lic.ucs", "-M", "01/05/2027"], h.calls)
        # license.c copies -n into char Lfile[80]: refuse a longer path.
        long_path = "/" + "x" * 79
        h = fl.host(long_path)
        r = L.apply(h, "pw", self._diff(fl, {}, "12/31/2027"), long_path)
        self.assertFalse(r["ok"])
        self.assertIn("too long", r["error"])
        self.assertEqual(h.calls, [])

    def test_non_ascii_password_rejected(self):
        fl = FakeLice(LIC)
        h = fl.host()
        d = self._diff(fl, {"NumNE": 500})
        for fn in (lambda: L.apply(h, "p\u00e9", d, "/p"),
                   lambda: L.unlock(h, "p\u00e9", "/p")):
            r = fn()
            self.assertFalse(r["ok"])
            self.assertIn("ASCII", r["error"])
            self.assertNotIn("p\u00e9", r["error"])
        self.assertEqual(L._pw_error("p\u00e9"), L.NON_ASCII_PW)
        self.assertEqual(h.calls, [])

    def test_fake_spins_on_short_input(self):
        fl = FakeLice(LIC)
        r = fl.host().run([L.LICENSE, "-n", "/p", "-M", "01/05/2027"], input="bad\n")
        self.assertEqual(r.rc, 124)
        self.assertEqual(fl.eof_spins, ["license"])

    def test_apply_rejects_malformed_diff(self):
        fl = FakeLice(LIC)
        h = fl.host()
        good = self._diff(fl, {"NumNE": 500}, "", {5, 7})
        bad = [None, [], dict(good, changes=None), dict(good, set_lines="x"),
               dict(good, changes=[{"key": "NumNE"}]),
               dict(good, changes=[{"after": 1}]),
               dict(good, changes=[{"key": "Nope", "after": 1}]),
               dict(good, ne_toggle=[True]), dict(good, ne_enable=["7"]),
               dict(good, ne_enable=None),
               dict(good, set_lines=["set numne"]),
               dict(good, set_lines=["numne 5"]),
               dict(good, set_lines=["set numne 5\rsave"]),
               dict(good, set_lines=["set numne 5\n"]),
               dict(good, set_lines=["set numne 5\nsave"]),
               dict(good, set_lines=[5]),
               dict(good, expiry="2027-01-05"), dict(good, expiry=5)]
        for d in bad:
            r = L.apply(h, "pw", d, "/p")
            self.assertFalse(r["ok"], repr(d))
            self.assertTrue(r["error"].startswith("invalid change set"), r["error"])
        self.assertEqual(h.calls, [])

    def test_password_too_long(self):
        fl = FakeLice(LIC)
        h = fl.host()
        d = self._diff(fl, {"NumNE": 500})
        self.assertEqual(L.apply(h, "x" * 255, d, "/p")["error"],
                         "license password too long")
        self.assertEqual(L.unlock(h, "x" * 255, "/p")["error"],
                         "license password too long")
        self.assertEqual(L.apply(h, "\u00e9" * 128, d, "/p")["error"],
                         "license password too long")
        self.assertEqual(h.calls, [])
        fl.password = "x" * 254
        self.assertTrue(L.apply(fl.host(), "x" * 254, d, "/p")["ok"])

    def test_apply_wrong_password(self):
        fl = FakeLice(LIC)
        before = json.loads(json.dumps(fl.lic))
        h = fl.host()
        d = self._diff(fl, {"NumNE": 500}, "1/5/2027", {5, 7})
        r = L.apply(h, "bad", d, "/p")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "wrong license password")
        self.assertEqual(r["applied"], [])
        self.assertEqual(r["not_applied"], ["NumNE", "NE type 7", "Expiry"])
        self.assertNotIn(L.LICENSE, [c[0] for c in h.calls])
        self.assertEqual(fl.lic, before)

    def test_apply_unrecognised_failure_shows_stderr(self):
        fl = FakeLice(LIC)
        h = fl.host()
        h.responses[(L.LICE, "-i", "/p")] = RunResult(1, "", "Error: weird\n")
        r = L.apply(h, "pw", self._diff(fl, {"NumNE": 500}), "/p")
        self.assertEqual((r["ok"], r["error"]), (False, "Error: weird"))

    def test_apply_reports_field_not_applied(self):
        fl = FakeLice(LIC, ignore=("numusers",))
        r = L.apply(fl.host(), "pw", self._diff(fl, {"NumNE": 7, "NumUsers": 3}), "/p")
        self.assertFalse(r["ok"])
        self.assertEqual(r["applied"], ["NumNE"])
        self.assertEqual(r["not_applied"], ["NumUsers"])
        self.assertIn("NumUsers", r["error"])

    def test_apply_no_changes_runs_nothing(self):
        fl = FakeLice(LIC)
        h = fl.host()
        r = L.apply(h, "pw", self._diff(fl, {"NumNE": 100}, "12/31/2026", {5}), "/p")
        self.assertEqual(r, {"ok": True, "applied": [], "not_applied": [], "error": ""})
        self.assertEqual(h.calls, [])

    def test_apply_bad_input_never_raises(self):
        fl = FakeLice(LIC)
        h = fl.host()
        d = self._diff(fl, {"NumNE": 500})
        for pw in (None, 12, "a\nb", "a\rb", ""):
            r = L.apply(h, pw, d, "/p")
            self.assertEqual((r["ok"], r["error"], r["applied"], r["not_applied"]),
                             (False, "invalid license password", [], []))
        r = L.apply(h, "pw", dict(d, ne_toggle=["5"]), "/p")
        self.assertFalse(r["ok"])
        self.assertEqual(h.calls, [])

    def test_password_never_in_result(self):
        pw = "s3cr3t-Pw"
        fl = FakeLice(LIC, password=pw)
        d = self._diff(fl, {"NumNE": 500}, "1/5/2027", {5, 7})
        for p in ("wrong-" + pw, pw):
            r = L.apply(fl.host(), p, d, "/p")
            self.assertNotIn(pw, json.dumps(r))
            self.assertNotIn(pw, json.dumps(L.unlock(fl.host(), p, "/p")))
        h = fl.host()
        h.responses[(L.LICE, "-i", "/p")] = RunResult(1, "", "echo " + pw)
        self.assertNotIn(pw, json.dumps(L.apply(h, pw, d, "/p")))
        self.assertNotIn(pw, json.dumps(L.unlock(h, pw, "/p")))

    def test_migrate_rc(self):
        for rc, ok in ((0, True), (6, True), (2, False)):
            h = FakeHost({("su", "cmm", "-c", L.MIGRATE): RunResult(rc, "", "e\n")})
            r = L.migrate(h)
            self.assertEqual((r["ok"], r["rc"], r["error"]), (ok, rc, "e"))


class TestLicenseFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def _write(self, name, data="x", mode=0o640):
        p = os.path.join(self.dir, name)
        with open(p, "w") as fh:
            fh.write(data)
        os.chmod(p, mode)
        return p

    def test_backup_keeps_ten(self):
        lic = self._write("lic.ucs", "LIC", 0o640)
        for i in range(12):
            self._write("lic.ucs.nfupgrader.202601010000{:02d}".format(i))
        other = self._write("lic.ucs.nfupgrader.notes")
        now = time.mktime((2026, 11, 4, 12, 30, 45, 0, 0, -1))
        b = L.backup(lic, now=now)
        self.assertEqual(b, lic + ".nfupgrader.20261104123045")
        with open(b) as fh:
            self.assertEqual(fh.read(), "LIC")
        st, sb = os.stat(lic), os.stat(b)
        self.assertEqual((sb.st_mode, sb.st_uid, sb.st_gid),
                         (st.st_mode, st.st_uid, st.st_gid))
        names = sorted(n for n in os.listdir(self.dir)
                       if n.startswith("lic.ucs.nfupgrader.2"))
        self.assertEqual(len(names), 10)
        self.assertEqual(names[-1], os.path.basename(b))
        self.assertEqual(names[0], "lic.ucs.nfupgrader.20260101000003")
        self.assertTrue(os.path.exists(other))
        # A second backup in the same second must not overwrite the first.
        b2 = L.backup(lic, now=now)
        self.assertNotEqual(b2, b)
        self.assertTrue(os.path.exists(b))

    def test_backup_chowns_to_original(self):
        lic = self._write("lic.ucs")
        st = os.stat(lic)
        with mock.patch.object(L.os, "chown") as ch:
            b = L.backup(lic, now=0)
        ch.assert_called_once_with(b, st.st_uid, st.st_gid)

    def test_create_from_template(self):
        t64 = self._write("def.linux64", "T64", 0o644)
        t32 = self._write("def.linux", "T32", 0o600)
        dst = os.path.join(self.dir, "lic.ucs")
        gid = os.stat(t64).st_gid
        pw = mock.Mock(pw_uid=4242)
        with mock.patch.object(L.pwd, "getpwnam", return_value=pw) as gp, \
                mock.patch.object(L.os, "chown") as ch:
            used = L.create_from_template(dst, (t64, t32))
        self.assertEqual(used, t64)
        gp.assert_called_once_with("cmm")
        ch.assert_called_once_with(dst, 4242, gid)
        with open(dst) as fh:
            self.assertEqual(fh.read(), "T64")
        self.assertEqual(os.stat(dst).st_mode & 0o7777, 0o644)
        with mock.patch.object(L.pwd, "getpwnam", return_value=pw), \
                mock.patch.object(L.os, "chown"):
            with self.assertRaises(ValueError):
                L.create_from_template(dst, (t64, t32))
            os.unlink(dst)
            missing = os.path.join(self.dir, "nope")
            self.assertEqual(L.create_from_template(dst, (missing, t32)), t32)
            self.assertEqual(os.stat(dst).st_mode & 0o7777, 0o600)
            os.unlink(dst)
            with self.assertRaises(ValueError) as cm:
                L.create_from_template(dst, (missing,))
            self.assertEqual(str(cm.exception), "no license template found")
        self.assertFalse(os.path.exists(dst))

    def test_create_from_template_unknown_owner(self):
        t = self._write("def.linux64")
        dst = os.path.join(self.dir, "lic.ucs")
        with mock.patch.object(L.pwd, "getpwnam", side_effect=KeyError("cmm")):
            with self.assertRaises(ValueError):
                L.create_from_template(dst, (t,))
        self.assertFalse(os.path.exists(dst))


if __name__ == "__main__":
    unittest.main()
