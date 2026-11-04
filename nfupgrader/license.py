"""license.py — read, validate, diff and script netFLEX license edits.

This module is the read/compute half of the License tab: everything here is
pure or read-only. The license password is never stored or logged here; it is
only placed as the first line of the stdin script built by build_script()."""
import json
import math
import re
import time

LICE = "/usr/cnc/mbin/lice"
LICENSE = "/usr/cnc/mbin/license"
MIGRATE = "/usr/cnc/mbin/license-migrate"
LIC_PATH = "/usr/cnc/data/lic.ucs"
TEMPLATES = ("/usr/cnc/data/def.lic.ucs.linux64",
             "/usr/cnc/data/def.lic.ucs.linux")
APP_UP = "/usr/cnc/.CNC_UP"


def _f(key, name, kind, label, main=False, max_len=None):
    d = {"key": key, "set": name, "kind": kind, "label": label, "main": main}
    if max_len is not None:
        d["max_len"] = max_len
    return d


FIELDS = [
    _f("NumNE", "numne", "int", "NE count", True),
    _f("NumUsers", "numusers", "int", "Users", True),
    _f("Trial", "trial", "bool", "Trial license", True),
    _f("NeAvail", "neavail", "int", "NEs available"),
    _f("NumWeightedNE", "numweightedne", "float", "Weighted NEs"),
    _f("NumCusers", "numcusers", "int", "Client users"),
    _f("NumGusers", "numgusers", "int", "GUI users"),
    _f("DgAvail", "dgavail", "int", "Digital groups available"),
    _f("NumUnits", "numunits", "int", "Units"),
    _f("NumLegs", "numlegs", "int", "Legs"),
    _f("NumTports", "numtports", "int", "Transport ports"),
    _f("SvcMax", "svcmax", "int", "Maximum services"),
    _f("DefMax", "defmax", "int", "Maximum definitions"),
    _f("AtMax", "atmax", "int", "Maximum AT sessions"),
    _f("NumSubNetworks", "numsubnetworks", "int", "Subnetworks"),
    _f("NumShelves", "numshelves", "int", "Shelves"),
    _f("NumFDP", "numfdp", "int", "FDPs"),
    _f("NumCLAN", "numclan", "int", "CLANs"),
    _f("Dna", "dna", "bool", "DNA"),
    _f("PmExport", "pmexport", "bool", "PM export"),
    _f("Tarentella", "tarentella", "bool", "Tarantella"),
    _f("EthernetSupport", "ethernet", "bool", "Ethernet support"),
    _f("Physical", "physical", "bool", "Physical layer"),
    _f("GroupNes", "groupnes", "bool", "Group NEs"),
    _f("GR", "gr", "bool", "Generic router (GR)"),
    _f("MDH", "mdh", "bool", "MDH"),
    _f("Discover", "discover", "bool", "Discovery"),
    _f("ODC", "odc", "bool", "ODC"),
    _f("WSO", "wso", "bool", "WSO"),
    _f("Errigal", "errigal", "bool", "Errigal"),
    _f("Version", "version", "str", "Version", max_len=20),
    _f("Text", "text", "str", "Text", max_len=128),
]
_BY_KEY = dict((f["key"], f) for f in FIELDS)

_EXP_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def read_license(host, lic_path=LIC_PATH):
    """Read the license via `lice -j`. Returns {"state","license","error"}."""
    def res(state, lic=None, err=""):
        return {"state": state, "license": lic, "error": err}

    if host.run(["test", "-x", LICE]).rc != 0:
        return res("unavailable")
    if host.run(["test", "-f", lic_path]).rc != 0:
        return res("missing")
    r = host.run([LICE, "-j", lic_path])
    if r.rc == 0:
        try:
            lic = json.loads(r.out)
        except ValueError as e:
            return res("unreadable", err="bad JSON from lice: {}".format(e))
        if not isinstance(lic, dict):
            return res("unreadable", err="unexpected JSON from lice")
        return res("ok", lic)
    if "earlier version of netFLEX" in (r.err or ""):
        return res("old_format", err=r.err.strip())
    return res("unreadable", err=(r.err or "").strip())


def expiry_of(lic):
    """MM/DD/YYYY from MaintExpireDate, or "" if absent/unparsable."""
    try:
        t = time.strptime(lic.get("MaintExpireDate", ""), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ""
    return time.strftime("%m/%d/%Y", t)


def enabled_types(lic):
    """Set of enabled NE type numbers."""
    return set(int(e["type"]) for e in lic.get("EnabledNeTypes") or [])


_NE_ROW = re.compile(
    r"^\s*(\d+) \| (.*) \| +(ENABLED|DISABLED) \| +(\d+) \| +(\d+)\s*$")


def parse_ne_all(text):
    """Parse the `ne all` table into a list of row dicts."""
    rows = []
    for line in (text or "").splitlines():
        m = _NE_ROW.match(line)
        if m:
            rows.append({"type": int(m.group(1)), "name": m.group(2).strip(),
                         "enabled": m.group(3) == "ENABLED",
                         "group": int(m.group(4)), "count": int(m.group(5))})
    return rows


def _norm_expiry(s):
    """Return zero-padded MM/DD/YYYY for a valid date (year >= 1970) or None."""
    if not isinstance(s, str):
        return None
    m = _EXP_RE.match(s.strip())
    if not m:
        return None
    norm = "{:02d}/{:02d}/{}".format(int(m.group(1)), int(m.group(2)), m.group(3))
    try:
        t = time.strptime(norm, "%m/%d/%Y")
    except ValueError:
        return None
    return norm if t.tm_year >= 1970 else None


def _as_int(v):
    """Non-negative int from an int or decimal string, else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, str) and re.match(r"^[0-9]+$", v.strip()):
        return int(v.strip())
    return None


def _as_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    elif isinstance(v, str):
        try:
            x = float(v.strip())
        except ValueError:
            return None
    else:
        return None
    return x if (math.isfinite(x) and x >= 0) else None


def _bad_char(s):
    return any(ord(c) < 0x20 or ord(c) == 0x7f for c in s)


def validate(values, expiry, ne_on):
    """Return [{"field","message"}] for anything invalid. ne_on None = skip."""
    errs = []

    def err(field, msg):
        errs.append({"field": field, "message": msg})

    for key in sorted(values):
        f = _BY_KEY.get(key)
        v = values[key]
        if f is None:
            err(key, "unknown field")
        elif f["kind"] == "int":
            if _as_int(v) is None:
                err(key, "must be a whole number, 0 or more")
        elif f["kind"] == "float":
            if _as_float(v) is None:
                err(key, "must be a number, 0 or more")
        elif f["kind"] == "bool":
            if not isinstance(v, bool):
                err(key, "must be true or false")
        else:
            if not isinstance(v, str):
                err(key, "must be text")
            elif len(v) > f["max_len"]:
                err(key, "at most {} characters".format(f["max_len"]))
            elif _bad_char(v):
                err(key, "must not contain control characters")
    if expiry not in ("", None) and _norm_expiry(expiry) is None:
        err("expiry", "must be a valid MM/DD/YYYY date, 1970 or later")
    if ne_on is not None:
        try:
            items = list(ne_on)
        except TypeError:
            items = None
        if items is None:
            err("ne_on", "must be a list of NE type numbers")
        else:
            for n in items:
                if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 255:
                    err("ne_on", "NE type must be a whole number 1 to 255")
                    break
    return errs


def _canon(f, v):
    k = f["kind"]
    if k == "int":
        return _as_int(v)
    if k == "float":
        return _as_float(v)
    return v


def _render(f, v):
    if f["kind"] == "bool":
        return "yes" if v else "no"
    if f["kind"] == "float":
        return "{:g}".format(v)
    return str(v)


def diff(lic, values, expiry, ne_on, ne_table=None):
    """Compute the edit between lic and the requested values (assumed valid).
    ne_on None means NE types are unchanged."""
    changes, set_lines, warnings = [], [], []
    for f in FIELDS:
        key = f["key"]
        if key not in values:
            continue
        after = _canon(f, values[key])
        before = lic.get(key)
        if f["kind"] in ("int", "float") and before is not None:
            before_c = _canon(f, before)
        else:
            before_c = before
        if before_c == after and key in lic:
            continue
        changes.append({"key": key, "label": f["label"],
                        "before": before, "after": after})
        set_lines.append("set {} {}".format(f["set"], _render(f, after)))
    cur = enabled_types(lic)
    if ne_on is None:
        toggle = []
    else:
        want = set(ne_on)
        toggle = sorted(cur ^ want)
        by_type = dict((r["type"], r) for r in (ne_table or []))
        for n in toggle:
            r = by_type.get(n)
            if n in want and r and r["group"] != 0:
                warnings.append(
                    "NE type {} ({}) is in group {}; that group's limit, if "
                    "any, still applies".format(n, r["name"], r["group"]))
    exp = _norm_expiry(expiry) if expiry else None
    if exp is not None and exp == expiry_of(lic):
        exp = None
    return {"changes": changes, "set_lines": set_lines, "ne_toggle": toggle,
            "expiry": exp, "warnings": warnings}


def build_script(password, d):
    """Stdin script for `lice`: password line, edits, save, quit."""
    s = password + "\n"
    for line in d["set_lines"]:
        s += line + "\n"
    for n in d["ne_toggle"]:
        s += "ne {}\nyes\n".format(n)
    return s + "save\nquit\n"
