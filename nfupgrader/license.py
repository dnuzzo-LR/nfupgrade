"""license.py — read, validate, diff and script netFLEX license edits.

The read/compute half (read_license .. build_script) is pure or read-only;
unlock/apply/backup/create_from_template/migrate are the write half. The
license password is never stored, logged or returned here; it only reaches the
tools as the first stdin line, and tool output is scrubbed of it."""
import glob
import json
import math
import os
import pwd
import re
import shutil
import stat
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

INT_MAX = 2147483647  # lice parses with stoi; larger values throw

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
        return v if 0 <= v <= INT_MAX else None
    if isinstance(v, str) and re.match(r"^[0-9]+$", v.strip()):
        n = int(v.strip())
        return n if n <= INT_MAX else None
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
    if not (math.isfinite(x) and 0 <= x <= INT_MAX):
        return None
    if abs(round(x, 2) - x) > 1e-9:
        return None  # lice keeps two decimals only
    return x


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
                err(key, "must be a whole number, 0 to {}".format(INT_MAX))
        elif f["kind"] == "float":
            if _as_float(v) is None:
                err(key, "must be a number, 0 or more, with at most 2 decimals")
        elif f["kind"] == "bool":
            if not isinstance(v, bool):
                err(key, "must be true or false")
        else:
            if not isinstance(v, str):
                err(key, "must be text")
            elif len(v.encode("utf-8")) > f["max_len"]:
                err(key, "at most {} bytes".format(f["max_len"]))
            elif v == "" or v != v.strip():
                err(key, "{} cannot be empty or start/end with spaces "
                    "-- lice cannot store that".format(f["label"]))
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
        return "{:.2f}".format(v)
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
        if f["kind"] == "float" and isinstance(before, (int, float)) \
                and not isinstance(before, bool):
            before_c = round(float(before), 2)
            after = round(after, 2)
        elif f["kind"] == "int" and before is not None:
            before_c = _canon(f, before)
        else:
            before_c = before
        if before_c == after and key in lic:
            continue
        changes.append({"key": key, "label": f["label"],
                        "before": before, "after": after})
        set_lines.append("set {} {}".format(f["set"], _render(f, after)))
    cur = enabled_types(lic)
    enable = []
    if ne_on is None:
        toggle = []
    else:
        want = set(ne_on)
        toggle = sorted(cur ^ want)
        enable = [n for n in toggle if n in want]
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
            "ne_enable": enable,
            "expiry": exp, "warnings": warnings}


def build_script(password, d):
    """Stdin script for `lice`: password line, edits, save, quit."""
    if "\n" in password or "\r" in password:
        raise ValueError("password must not contain a newline")
    for n in d["ne_toggle"]:
        if isinstance(n, bool) or not isinstance(n, int):
            raise ValueError("ne_toggle entries must be ints")
    # Input must hold >= 3 lines before EOF: on a wrong password lice reads up
    # to 3 attempts, and at EOF during the prompt it loops forever
    # (lice.cpp:369-372). Password + "save" + "quit" guarantees that.
    s = password + "\n"
    for line in d["set_lines"]:
        s += line + "\n"
    for n in d["ne_toggle"]:
        s += "ne {}\nyes\n".format(n)
    return s + "save\nquit\n"


AUTH_OK = "Authentication successful."
_BAD_PW = ("Invalid password.", "Too many incorrect attempts.")
WRONG_PW = "wrong license password"
INVALID_PW = "invalid license password"
TOO_LONG_PW = "license password too long"
PW_MAX_BYTES = 254      # fgets into a 256-byte buffer, incl. newline and NUL
LICE_TIMEOUT = 120      # lice/license loop forever on stdin EOF at the prompt
MIGRATE_TIMEOUT = 600
_SET_LINE = re.compile(r"^set \S+ \S.*$")


def _pw_error(password):
    """Error string if the password cannot be sent as one stdin line, else ""."""
    if not isinstance(password, str) or password == "" \
            or "\n" in password or "\r" in password:
        return INVALID_PW
    if len(password.encode("utf-8", "replace")) > PW_MAX_BYTES:
        return TOO_LONG_PW
    return ""


def _int_list(v, name):
    if not isinstance(v, list) or any(
            isinstance(n, bool) or not isinstance(n, int) for n in v):
        raise ValueError("{} must be a list of NE type numbers".format(name))


def _check_diff(d):
    """Raise ValueError unless d is a well-formed diff() result."""
    if not isinstance(d, dict):
        raise ValueError("change set must be an object")
    for k in ("changes", "set_lines", "ne_toggle"):
        if not isinstance(d.get(k), list):
            raise ValueError("{} must be a list".format(k))
    for c in d["changes"]:
        if not isinstance(c, dict) or "key" not in c or "after" not in c:
            raise ValueError("each change needs key and after")
        if c["key"] not in _BY_KEY:
            raise ValueError("unknown field {}".format(c["key"]))
    for line in d["set_lines"]:
        if not isinstance(line, str) or "\n" in line or "\r" in line \
                or not _SET_LINE.match(line):
            raise ValueError("malformed set line")
    _int_list(d["ne_toggle"], "ne_toggle")
    if "ne_enable" in d:
        _int_list(d["ne_enable"], "ne_enable")
    exp = d.get("expiry")
    if exp is not None and (not isinstance(exp, str) or _norm_expiry(exp) != exp):
        raise ValueError("expiry must be MM/DD/YYYY")


def _scrub(text, password):
    """Strip whitespace and mask the password should a tool ever echo it."""
    text = (text or "").strip()
    if isinstance(password, str) and password:
        text = text.replace(password, "***")
    return text


def _wrong_pw(err):
    return any(m in (err or "") for m in _BAD_PW)


def unlock(host, password, lic_path=LIC_PATH):
    """Check the password with `lice -i` and list NE types (`ne all`)."""
    pw_err = _pw_error(password)
    if pw_err:
        return {"ok": False, "ne_types": [], "error": pw_err}
    # Input must hold >= 3 lines before EOF (3 password attempts; lice loops
    # forever on EOF at the prompt, lice.cpp:369-372).
    r = host.run([LICE, "-i", lic_path], timeout=LICE_TIMEOUT,
                 input=password + "\nne all\nquit\n")
    if AUTH_OK in (r.out or ""):
        return {"ok": True, "ne_types": parse_ne_all(r.out), "error": ""}
    if _wrong_pw(r.err):
        err = WRONG_PW
    else:
        err = _scrub(r.err, password) or "could not unlock the license"
    return {"ok": False, "ne_types": [], "error": err}


_BACKUP_RE = re.compile(r"\.nfupgrader\.\d{14}(-\d+)?$")


def _backup_key(path):
    """Sort key: timestamp, then same-second sequence number."""
    m = _BACKUP_RE.search(path)
    stamp = m.group(0)[len(".nfupgrader."):][:14]
    return (stamp, int(m.group(1)[1:]) if m.group(1) else 0)


def backup(lic_path, now=None, keep=10):
    """Copy lic_path to <lic_path>.nfupgrader.<YYYYmmddHHMMSS> (same owner and
    mode), keep only the newest `keep` such backups, return the new path."""
    stamp = time.strftime("%Y%m%d%H%M%S",
                          time.localtime(time.time() if now is None else now))
    base = "{}.nfupgrader.{}".format(lic_path, stamp)
    dst, n = base, 0
    while os.path.lexists(dst):  # never overwrite an earlier backup
        n += 1
        dst = "{}-{}".format(base, n)
    st = os.stat(lic_path)
    shutil.copy2(lic_path, dst)
    os.chown(dst, st.st_uid, st.st_gid)
    olds = [p for p in glob.glob(glob.escape(lic_path) + ".nfupgrader.*")
            if _BACKUP_RE.match(p[len(lic_path):])]
    olds.sort(key=_backup_key)
    for p in olds[:-keep] if keep > 0 else olds:
        if p != dst:
            os.unlink(p)
    return dst


def create_from_template(lic_path=LIC_PATH, templates=TEMPLATES, owner="cmm"):
    """Copy the first existing template to lic_path: template's mode and gid,
    owner's uid. Refuses (ValueError) to overwrite. Returns the template."""
    tpl = None
    for t in templates:
        if os.path.isfile(t):
            tpl = t
            break
    if tpl is None:
        raise ValueError("no license template found")
    try:
        uid = pwd.getpwnam(owner).pw_uid
    except KeyError:
        raise ValueError("user {} not found".format(owner))
    st = os.stat(tpl)
    try:
        fd = os.open(lic_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("{} already exists".format(lic_path))
    try:
        with os.fdopen(fd, "wb") as out, open(tpl, "rb") as src:
            shutil.copyfileobj(src, out)
        os.chown(lic_path, uid, st.st_gid)
        os.chmod(lic_path, stat.S_IMODE(st.st_mode))  # after chown: keeps bits
    except BaseException:
        os.unlink(lic_path)
        raise
    return tpl


def migrate(host):
    """Run license-migrate as cmm; exit 0 or 6 counts as success."""
    r = host.run(["su", "cmm", "-c", MIGRATE], timeout=MIGRATE_TIMEOUT)
    return {"ok": r.rc in (0, 6), "rc": r.rc, "error": (r.err or "").strip()}


def _same(f, want, got):
    """Does the read-back value `got` match the requested `want`?"""
    if f["kind"] == "float":
        num = (int, float)
        return isinstance(got, num) and not isinstance(got, bool) \
            and isinstance(want, num) \
            and round(float(got), 2) == round(float(want), 2)
    if f["kind"] == "int":
        return _as_int(got) is not None and _as_int(got) == _as_int(want)
    if f["kind"] == "bool":
        return isinstance(got, bool) and got == want
    return got == want


def apply(host, password, d, lic_path=LIC_PATH):
    """Apply a diff() result with lice -i / license -M, then verify by reading
    the license back. Never raises for bad input; never returns the password."""
    def fail(err):
        return {"ok": False, "error": err, "applied": [], "not_applied": [],
                "license": None}

    pw_err = _pw_error(password)
    if pw_err:
        return fail(pw_err)
    try:
        _check_diff(d)
        changes, toggles, exp = d["changes"], d["ne_toggle"], d.get("expiry")
        if not (changes or toggles or exp):
            return {"ok": True, "applied": [], "not_applied": [], "error": ""}
        script = build_script(password, d) if (d["set_lines"] or toggles) \
            else None
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return fail(_scrub("invalid change set: {}".format(e), password))

    if script is None and exp:
        # Expiry-only: license -M would be the first password check, and on
        # a wrong password plus stdin EOF license.c:424-426 spins forever
        # printing a prompt. Verify the password with lice first.
        u = unlock(host, password, lic_path)
        if not u["ok"]:
            res = fail(u["error"])
            res["not_applied"] = ["Expiry"]
            return res

    if "ne_enable" in d:
        want_on = set(d["ne_enable"])
    elif toggles:  # hand-built d: a toggle flips the current state
        pre = read_license(host, lic_path)
        cur = enabled_types(pre["license"] or {}) if pre["state"] == "ok" else set()
        want_on = set(n for n in toggles if n not in cur)
    else:
        want_on = set()

    errors = []
    authed = True
    if script is not None:
        r = host.run([LICE, "-i", lic_path], timeout=LICE_TIMEOUT, input=script)
        authed = AUTH_OK in (r.out or "")
        if not authed and _wrong_pw(r.err):
            errors.append(WRONG_PW)
        elif not authed:
            errors.append(_scrub(r.err, password) or
                          "lice did not accept the password; "
                          "the changes did not apply")
        elif _scrub(r.err, password):
            errors.append(_scrub(r.err, password))
    if authed and exp:
        # Input must hold >= 3 lines before EOF: license_unlock tries 3 times
        # and loops forever on EOF (license.c:424-426). After a successful
        # unlock, -M reads no more stdin (set_license SET_MAINTENANCE_DATE,
        # load_license_mem(0) does not prompt), so the extra lines are unread.
        r = host.run([LICENSE, "-M", exp], timeout=LICE_TIMEOUT,
                     input=(password + "\n") * 3)
        if _wrong_pw(r.err):
            errors.append(WRONG_PW)
        elif r.rc != 0:
            errors.append(_scrub(r.err, password) or
                          "license -M exited {}".format(r.rc))

    rb = read_license(host, lic_path)
    lic = rb["license"] if rb["state"] == "ok" else None
    if lic is None:
        errors.append(_scrub("could not read the license back: {}".format(
            rb["error"] or rb["state"]), password))
    applied, not_applied = [], []

    def check(name, ok):
        (applied if ok else not_applied).append(name)

    for c in changes:
        check(c["key"], lic is not None and
              _same(_BY_KEY[c["key"]], c["after"], lic.get(c["key"])))
    on = enabled_types(lic) if lic is not None else set()
    for n in toggles:
        check("NE type {}".format(n), lic is not None and
              (n in on) == (n in want_on))
    if exp:
        check("Expiry", lic is not None and expiry_of(lic) == exp)
    if not_applied and not errors:
        errors.append("not applied: {}".format(", ".join(not_applied)))
    return {"ok": not not_applied,
            "applied": applied, "not_applied": not_applied,
            "error": "\n".join(errors), "license": lic}
