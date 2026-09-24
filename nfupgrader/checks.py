"""checks.py — pre-flight/post-flight check engine. Each kind is a small
evaluator fn(host, params) -> (passed: bool, detail: str) run through Host.run.
A JSON list binds kinds to a moment (preflight/postflight), a phase, and an
optional MACHTYPE. Pre-flight ERRORs block Launch (override logged); everything
post-flight is advisory. Some kinds also have a fix (FIXES) that can be applied
from the UI."""
import errno
import glob
import json
import os
import re
import stat

from nfupgrader import localinfo


def _rpm_installed(host, p):
    r = host.run(["rpm", "-q", p["pkg"]])
    return r.rc == 0, r.out.strip() or r.err.strip()


def _file_exists(host, p):
    return host.run(["test", "-f", p["path"]]).rc == 0, p["path"]


def _file_absent(host, p):
    return host.run(["test", "-f", p["path"]]).rc != 0, p["path"]


def _symlink_target(host, p):
    r = host.run(["readlink", p["path"]])
    tgt = r.out.strip()
    if r.rc != 0 or not tgt:
        return False, "no symlink at {}".format(p["path"])
    if "contains" in p and p["contains"] not in tgt:
        return False, "{} -> {}".format(p["path"], tgt)
    return True, "{} -> {}".format(p["path"], tgt)


def _command_status(host, p):
    r = host.run(list(p["argv"]))
    return r.rc == p.get("expect_rc", 0), "rc={}".format(r.rc)


def _command_output_match(host, p):
    r = host.run(list(p["argv"]))
    found = re.search(p["pattern"], r.out + r.err) is not None
    if p.get("negate"):
        return (not found), ("matched" if found else "absent")
    return found, ("matched" if found else "not found")


def _disk_space_min(host, p):
    rows = localinfo.parse_df(host.run(["df", "-kP"]).out, keep=(p["mount"],))
    if not rows or rows[0]["avail_kb"] is None:
        return False, "no df row for {}".format(p["mount"])
    free_gb = rows[0]["avail_kb"] / (1024.0 * 1024.0)
    return free_gb >= p["min_gb"], "{:.1f} GB free".format(free_gb)


def _service_active(host, p):
    r = host.run(["systemctl", "is-active", p["name"]])
    return r.rc == 0, r.out.strip()


def _dbcheck_clean(host, p):
    n = localinfo.parse_dbcheck_errors(host.run(["dbcheck", "-AV"]).out)
    return n == 0, "{} error(s)".format(n)


def _staged_present(host, p):
    r = host.run(["readlink", "/usr/cnc_stage"])
    return (r.rc == 0 and bool(r.out.strip())), r.out.strip() or "no staged load"


def _touchfile_name_ok(name):
    """A touchfile is a plain name directly under /usr/cnc/features."""
    return bool(name) and "/" not in name and name not in (".", "..")


def _touchfile(host, p):
    """A netFLEX mode flag under /usr/cnc/features (ported from nf-install)."""
    if not _touchfile_name_ok(p["file"]):
        return False, "invalid touchfile name"
    path = "/usr/cnc/features/" + p["file"]
    return host.run(["test", "-f", path]).rc == 0, path


def _system_define(host, p):
    """A variable in /usr/cnc/features/system_defines whose value must be one of
    expected_values (an absent variable reads as '')."""
    var = p["variable"]
    expected = p.get("expected_values", [])
    r = host.run(["sh", "-c",
                  "grep -E '^%s=' /usr/cnc/features/system_defines 2>/dev/null | tail -1" % var])
    line = r.out.strip()
    val = line.split("=", 1)[1].strip() if "=" in line else ""
    return val in expected, "{}={}".format(var, val or "(unset)")


def _kernel_param(host, p):
    """A sysctl parameter that must equal expected_value."""
    r = host.run(["sysctl", p["param"]])
    if r.rc != 0:
        return False, "cannot read {}".format(p["param"])
    out = r.out.strip()
    cur = out.split(" = ", 1)[1] if " = " in out else out
    # Multi-value params (e.g. net.ipv4.tcp_rmem) print tab-separated.
    return " ".join(cur.split()) == " ".join(p["expected_value"].split()), \
        "{} = {}".format(p["param"], cur)


# pam_limits reads limits.conf, then limits.d/*.conf in sorted order; a later
# entry for the same domain/type/item wins. Base paths are overridable for tests.
LIMITS_MAIN = "/etc/security/limits.conf"
LIMITS_DIR = "/etc/security/limits.d"


def _read_text(path):
    """The text of path (undecodable bytes kept as surrogates), or None if unreadable."""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            return fh.read()
    except (IOError, OSError):
        return None


def _limits_files():
    """The files pam_limits reads, in the order it reads them."""
    return [LIMITS_MAIN] + sorted(glob.glob(os.path.join(LIMITS_DIR, "*.conf")))


def _ulimit_winner(p):
    """(file, value) of the effective (last) entry for p's domain/type/item, or None."""
    head = [p["domain"], p["limit_type"], p["limit_item"]]
    found = None
    for path in _limits_files():
        text = _read_text(path)
        for line in (text or "").splitlines():
            parts = line.split()
            if parts and parts[0].startswith("#"):
                continue
            if len(parts) >= 4 and parts[:3] == head:
                found = (path, parts[3])
    return found


def _ulimit(host, p):
    """A limits.conf / limits.d entry (domain type item value) that must equal
    expected_value. Read locally (like its fix) from _limits_files()."""
    found = _ulimit_winner(p)
    if found is None:
        return False, "{} {} {} not set".format(p["domain"], p["limit_item"], p["limit_type"])
    return found[1] == p["expected_value"], "{} {} = {}".format(p["domain"], p["limit_item"],
                                                               found[1])


KINDS = {
    "rpm_installed": _rpm_installed,
    "file_exists": _file_exists,
    "file_absent": _file_absent,
    "symlink_target": _symlink_target,
    "command_status": _command_status,
    "command_output_match": _command_output_match,
    "disk_space_min": _disk_space_min,
    "service_active": _service_active,
    "dbcheck_clean": _dbcheck_clean,
    "staged_present": _staged_present,
    "touchfile": _touchfile,
    "system_define": _system_define,
    "kernel_param": _kernel_param,
    "ulimit": _ulimit,
}


# --- fixes (ported from nf-install system_checks fix_*; the service runs as root) ---

SYSCTL_CONF = "/etc/sysctl.d/99-netflex.conf"
LIMITS_CONF = "/etc/security/limits.d/99-netflex.conf"
SCMD = "/usr/cnc/bin/scmd"
DNF = "/usr/bin/dnf"          # RHEL 8/9; RHEL 7 only has yum
# What is applied at boot, highest priority first: systemd-sysctl merges these by
# basename (the first dir holding a name wins) and reads them sorted by basename;
# /etc/sysctl.conf comes last (normally already read via 99-sysctl.conf -> ../sysctl.conf).
SYSCTL_DIRS = ("/etc/sysctl.d", "/run/sysctl.d", "/usr/local/lib/sysctl.d", "/usr/lib/sysctl.d")
SYSCTL_MAIN = "/etc/sysctl.conf"


def _cmd_detail(r):
    """'rc=N: <last lines of stderr, else stdout>' for a failed fix command."""
    lines = (r.err.strip() or r.out.strip()).splitlines()[-3:]
    text = " | ".join(ln.strip() for ln in lines)
    if len(text) > 300:
        text = text[-300:]
    return "rc={}: {}".format(r.rc, text) if text else "rc={}".format(r.rc)


def _replace_lines(path, keep, new_line):
    """Rewrite path keeping the lines for which keep(line) is true, then append
    new_line. A symlink is followed (the link stays); bytes that are not UTF-8 and
    line endings survive untouched; an existing file keeps its mode and owner.
    Written to a temp file, fsynced and renamed so a reader never sees half a file.
    Only a missing file reads as empty; any other read error is raised."""
    path = os.path.realpath(path)
    try:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as fh:
            text = fh.read()
        st = os.stat(path)
    except (IOError, OSError) as e:
        if e.errno != errno.ENOENT:
            raise
        text, st = "", None
    parts = text.split("\n")
    lines = [ln + "\n" for ln in parts[:-1]] + ([parts[-1] + "\n"] if parts[-1] else [])
    lines = [ln for ln in lines if keep(ln)]
    lines.append(new_line + "\n")
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".nfupgrader.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            if st is not None:
                os.chmod(tmp, stat.S_IMODE(st.st_mode))
                try:
                    os.chown(tmp, st.st_uid, st.st_gid)
                except OSError:
                    pass
            fh.writelines(lines)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _arg_ok(value):
    """A package/unit name that cannot be taken for an option."""
    return bool(value) and not value.startswith("-")


def _pkg_tool():
    return "dnf" if os.path.exists(DNF) else "yum"


def _fix_rpm(host, p):
    tool = _pkg_tool()
    r = host.run([tool, "install", "-y", p["pkg"]], timeout=600)
    if r.rc != 0:
        return False, _cmd_detail(r)
    return True, "{} installed".format(p["pkg"])


def _fix_service(host, p):
    name = p["name"]
    r = host.run(["systemctl", "start", name], timeout=90)     # a unit may take a while to start
    if r.rc != 0:
        return False, "start failed, " + _cmd_detail(r)
    r = host.run(["systemctl", "enable", name], timeout=30)
    if r.rc != 0:
        return True, "WARNING: {} started but not enabled, {}".format(name, _cmd_detail(r))
    return True, "{} started and enabled".format(name)


def _sysctl_key(line):
    """The normalized key a sysctl.d line sets ('kernel/shmmax' == 'kernel.shmmax';
    whitespace and a leading '-' ignored), or None for comments/non-assignments."""
    s = line.strip()
    if not s or s[0] in "#;" or "=" not in s:
        return None
    key = re.sub(r"\s+", "", s.split("=", 1)[0]).lstrip("-")
    return key.replace("/", ".") if key else None


def _sysctl_files():
    """The sysctl files applied at boot, in the order they are applied."""
    by_name = {}
    for d in SYSCTL_DIRS:
        for path in glob.glob(os.path.join(d, "*.conf")):
            by_name.setdefault(os.path.basename(path), path)
    files, seen = [], set()
    for path in [by_name[b] for b in sorted(by_name)] + [SYSCTL_MAIN]:
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            files.append(path)
    return files


def _sysctl_boot_winner(param):
    """(file, value) of the last definition of param applied at boot, or None."""
    want = _sysctl_key(param + "=")
    found = None
    for path in _sysctl_files():
        for line in (_read_text(path) or "").splitlines():
            if _sysctl_key(line) == want:
                found = (path, line.split("=", 1)[1].strip())
    return found


def _same_file(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def _fix_kernel_param(host, p):
    setting = "{}={}".format(p["param"], p["expected_value"])
    r = host.run(["sysctl", "-w", setting], timeout=10)
    if r.rc != 0:
        return False, _cmd_detail(r)
    key = _sysctl_key(setting)
    try:
        _replace_lines(SYSCTL_CONF, lambda ln: _sysctl_key(ln) != key, setting)
    except (IOError, OSError, ValueError) as e:
        return True, "WARNING: {} set live but not persisted: {}".format(setting, e)
    win = _sysctl_boot_winner(p["param"])
    if win is None:
        return True, "WARNING: set live and persisted in {}, but {} is not read at boot".format(
            SYSCTL_CONF, SYSCTL_CONF)
    if not _same_file(win[0], SYSCTL_CONF) and \
            " ".join(win[1].split()) != " ".join(p["expected_value"].split()):
        return True, "WARNING: set live and persisted in {}, but {} sets {}={} at boot".format(
            SYSCTL_CONF, win[0], p["param"], win[1])
    return True, "{} set and persisted in {}".format(setting, SYSCTL_CONF)


def _fix_ulimit(host, p):
    head = [p["domain"], p["limit_type"], p["limit_item"]]
    entry = " ".join(head + [p["expected_value"]])

    def keep(ln):
        parts = ln.split()
        return not (len(parts) >= 4 and parts[:3] == head)
    try:
        _replace_lines(LIMITS_CONF, keep, entry)
    except (IOError, OSError, ValueError) as e:
        return False, "could not write {}: {}".format(LIMITS_CONF, e)
    win = _ulimit_winner(p)
    if win is None:
        return True, "WARNING: written to {} but it is not read by pam_limits".format(LIMITS_CONF)
    if win[1] != p["expected_value"]:
        return True, "WARNING: written to {} but overridden by {} ({})".format(
            LIMITS_CONF, win[0], win[1])
    return True, "'{}' written to {}".format(entry, LIMITS_CONF)


def _fix_touchfile(host, p):
    path = "/usr/cnc/features/" + p["file"]
    r = host.run([SCMD, "touch", path], timeout=30)
    if r.rc != 0:
        return False, _cmd_detail(r)
    return True, "{} created".format(path)


# kind -> (describe(params) -> str, apply(host, params) -> (ok, detail))
FIXES = {
    "rpm_installed": (lambda p: "{} install -y {}".format(_pkg_tool(), p["pkg"]), _fix_rpm),
    "service_active": (lambda p: "systemctl start {0} && systemctl enable {0}".format(p["name"]),
                       _fix_service),
    "kernel_param": (lambda p: "sysctl -w {}={} (persisted in {})".format(
        p["param"], p["expected_value"], SYSCTL_CONF), _fix_kernel_param),
    "ulimit": (lambda p: "add '{} {} {} {}' to {}".format(
        p["domain"], p["limit_type"], p["limit_item"], p["expected_value"], LIMITS_CONF),
               _fix_ulimit),
    "touchfile": (lambda p: "{} touch /usr/cnc/features/{}".format(SCMD, p["file"]),
                  _fix_touchfile),
}

# kind -> params -> bool: names that must never reach a root command line.
_FIX_ARGS_OK = {
    "rpm_installed": lambda p: _arg_ok(p["pkg"]),
    "service_active": lambda p: _arg_ok(p["name"]),
    "touchfile": lambda p: _touchfile_name_ok(p["file"]),
}


def _fix_args_ok(kind, p):
    ok = _FIX_ARGS_OK.get(kind)
    return ok is None or ok(p)


def describe_fix(check):
    """One line saying what the fix for this check would do, or None."""
    f = FIXES.get(check.get("kind"))
    if f is None:
        return None
    p = check.get("params", {})
    try:
        return f[0](p) if _fix_args_ok(check.get("kind"), p) else None
    except (KeyError, TypeError, AttributeError):
        return None


def apply_fix(host, check):
    """Run the fix for a check; returns (ok, detail). Never raises."""
    f = FIXES.get(check.get("kind"))
    if f is None:
        return False, "no fix for kind {}".format(check.get("kind"))
    p = check.get("params", {})
    try:
        if not _fix_args_ok(check.get("kind"), p):
            return False, "invalid name for a {} fix".format(check.get("kind"))
        return f[1](host, p)
    except Exception as e:
        return False, "fix error: {}".format(e)


def load_checks(path):
    """Load a JSON list of check definitions."""
    with open(path) as fh:
        return json.load(fh)


def _applies(check, when, phase, machtype):
    if check.get("when") != when:
        return False
    cphase = check.get("phase", "any")
    if cphase not in ("any", phase):
        return False
    cmt = check.get("machtype")
    if cmt is not None and cmt != machtype:
        return False
    return True


def evaluate(host, c, when):
    """Run one check definition; returns its result dict:
    {name, description, severity, passed, detail, blocking, fix}."""
    kind = KINDS.get(c["kind"])
    if kind is None:
        passed, detail = False, "unknown kind: {}".format(c["kind"])
    else:
        try:
            passed, detail = kind(host, c.get("params", {}))
        except Exception as e:                       # a check must never crash the run
            passed, detail = False, "check error: {}".format(e)
    severity = c.get("fail_text", "ERROR")
    return {
        "name": c.get("name", c["kind"]),
        "description": c.get("description", ""),
        "severity": severity,
        "passed": passed,
        "detail": detail,
        "blocking": (when == "preflight" and severity == "ERROR" and not passed),
        "fix": None if passed else describe_fix(c),
    }


def run_checks(host, check_list, when, phase, machtype=None):
    """Evaluate every check bound to (when, phase, machtype)."""
    return [evaluate(host, c, when) for c in check_list if _applies(c, when, phase, machtype)]


def find_check(check_list, name, when, phase, machtype=None):
    """The definition named `name` that applies to (when, phase, machtype), or None."""
    for c in check_list:
        if c.get("name", c["kind"]) == name and _applies(c, when, phase, machtype):
            return c
    return None


def blocking_failures(results):
    """The subset of results that block a launch."""
    return [r for r in results if r["blocking"]]
