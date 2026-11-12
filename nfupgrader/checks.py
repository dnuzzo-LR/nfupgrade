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


#: Detail for a missing package from EPEL (params "source": "epel"). Such
#: packages are outside Red Hat's supported content; on a customer (e.g. STIG)
#: host they may need approval, so the tool never offers to install them.
EPEL_MISSING = ("not installed \u2014 optional EPEL package (outside Red Hat-supported "
                "content); needs the customer's approval. To install: dnf install {}")


def _is_epel(p):
    return p.get("source") == "epel"


def _rpm_installed(host, p):
    """The package is installed; with "any_of", any one of those packages
    (e.g. nmap-ncat or EPEL's netcat for nc). The fix installs "pkg"."""
    r = None
    for pkg in p.get("any_of") or [p["pkg"]]:
        r = host.run(["rpm", "-q", pkg])
        if r.rc == 0:
            return True, r.out.strip() or r.err.strip()
    if _is_epel(p):
        return False, EPEL_MISSING.format(p["pkg"])
    return False, r.out.strip() or r.err.strip()


def _file_exists(host, p):
    return host.run(["test", "-f", p["path"]]).rc == 0, p["path"]


def _app_running(host, p):
    """netFLEX is running by its own rule (localinfo.app_state), not merely
    because .CNC_UP exists."""
    state = localinfo.app_state(host)
    if state == "stale":
        return False, "stale .CNC_UP (no netFLEX processes)"
    return state == "up", state


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
    r = host.run(["dbcheck", "-AV"])
    n = localinfo.parse_dbcheck_errors(r.out, r.rc)
    if n is None:
        # No usable summary: the database was not checked, so it is not clean.
        first = next((ln.strip() for ln in (r.out + "\n" + r.err).splitlines() if ln.strip()),
                     "exit {}".format(r.rc))
        return False, "dbcheck did not complete: {}".format(first)
    return n == 0, "{} error(s)".format(n)


def _staged_present(host, p):
    r = host.run(["readlink", "/usr/cnc_stage"])
    return (r.rc == 0 and bool(r.out.strip())), r.out.strip() or "no staged load"


def _rpm_qi_versions(host, pkg):
    """Each value `rpm -qi <pkg> | grep Version | cut -d':' -f2 | xargs` yields,
    in order (nf_upgrade then takes `tail -1`)."""
    out = []
    for line in host.run(["rpm", "-qi", pkg]).out.splitlines():
        if "Version" in line:
            parts = line.split(":")
            out.append(" ".join((parts[1] if len(parts) > 1 else line).split()))
    return out


def _is_netflex(host):
    return host.run(["test", "-f", "/usr/cnc/features/netFLEX"]).rc == 0


def _cut_generic_load(rev, netflex):
    """nf_upgrade's cuts: netFLEX -c1-5 / -c7-8, INC -c1-4 / -c6-7."""
    if netflex:
        return rev[0:5], rev[6:8]
    return rev[0:4], rev[5:7]


def upgrade_from_version(host):
    """The (generic, load) nf_upgrade's process_current_release will upgrade
    from, computed the same way: the last netFLEX-CORE rpm record, its load
    raised by a PATCH/IPATCH of the same generic. None if no CORE record.
    nf_upgrade must stay identical to netFLEX's, so the tool predicts it here
    rather than fixing it there (2026-11-11 test, finding #1)."""
    core = _rpm_qi_versions(host, "netFLEX-CORE")
    if not core:
        return None
    netflex = _is_netflex(host)
    gen, load = _cut_generic_load(core[-1], netflex)
    for pkg in ("netFLEX-CORE-PATCH", "netFLEX-CORE-IPATCH"):
        revs = _rpm_qi_versions(host, pkg)
        if not revs:
            continue
        rgen, rload = _cut_generic_load(revs[-1], netflex)
        try:
            if rgen == gen and int(rload) > int(load):
                load = rload
        except ValueError:                 # ksh's -gt errors and leaves OLD_LOAD
            pass
    return gen, load


def _version_key(gen, load):
    return tuple(int(x) for x in gen.split(".")) + (int(load),)


def _target_newer(host, p):
    """Stage pre-flight (hard): the target must be newer than the version
    nf_upgrade will upgrade from. Equal means it would skip the retrofits."""
    t = p.get("target") or {}
    ngen, nload = (t.get("generic") or "").strip(), (t.get("load") or "").strip()
    if not ngen or not nload:
        return False, "no target version: save the stage configuration (NEW_GENERIC, NEW_LOAD) first"
    cur = upgrade_from_version(host)
    if cur is None:
        return False, "cannot read the installed netFLEX-CORE version (rpm -qi netFLEX-CORE)"
    ogen, oload = cur
    old, new = "{}.{}".format(ogen, oload), "{}.{}".format(ngen, nload)
    try:
        newer = _version_key(ngen, nload) > _version_key(ogen, oload)
    except ValueError:
        return False, "cannot compare installed {} with target {}".format(old, new)
    if newer:
        return True, "stage upgrades from {} to {}".format(old, new)
    return False, ("stage would upgrade from {} to {}: the target is not newer, and nf_upgrade "
                   "would skip the database retrofits. netFLEX-CORE in the RPM database: {}"
                   .format(old, new, ", ".join(_rpm_qi_versions(host, "netFLEX-CORE"))))


def _rpm_matches_load(host, p):
    """Stage pre-flight: the netFLEX-CORE rpm record(s) must be the running
    load /usr/cnc points to. A record left by a failed or cleaned-up stage
    makes nf_upgrade compute the wrong from-version and skip retrofits.
    Overridable: load directory names are matched by convention only."""
    recs = [l.strip() for l in host.run(
        ["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}\\n", "netFLEX-CORE"]).out.splitlines() if l.strip()]
    core = _rpm_qi_versions(host, "netFLEX-CORE")
    if not core or not recs:
        return False, "no netFLEX-CORE in the RPM database"
    link = host.run(["readlink", "/usr/cnc"]).out.strip()
    name = link.rstrip("/").rsplit("/", 1)[-1]
    netflex = _is_netflex(host)

    def is_running(ver):
        g, l = _cut_generic_load(ver, netflex)
        return bool(link) and name in ("inc" + g + l, "inc" + g + "." + l)

    problems = []
    if len(core) > 1:
        problems.append("the RPM database lists {} netFLEX-CORE records ({}); nf_upgrade reads "
                        "the last".format(len(core), ", ".join(core)))
    if not is_running(core[-1]):
        problems.append("netFLEX-CORE {} is not the running load {}".format(core[-1], link or "(no /usr/cnc link)"))
    if not problems:
        return True, "netFLEX-CORE {} = {}".format(core[-1], link)
    stale = [r for r in recs if not is_running(r.rsplit("-", 1)[0])]
    hint = ("if left from an earlier stage, remove the record: " +
            "; ".join("rpm -e --justdb --noscripts netFLEX-CORE-" + r for r in stale)) if stale else ""
    return False, "; ".join(problems + ([hint] if hint else []))


def _touchfile_name_ok(name):
    """A touchfile is a plain name directly under /usr/cnc/features."""
    return bool(name) and "/" not in name and name not in (".", "..")


def _touchfile(host, p):
    """A netFLEX mode flag under /usr/cnc/features (ported from nf-install)."""
    if not _touchfile_name_ok(p["file"]):
        return False, "invalid touchfile name"
    path = "/usr/cnc/features/" + p["file"]
    return host.run(["test", "-f", path]).rc == 0, path


# netFLEX's feature settings file; module-level so tests can point it elsewhere.
SYSTEM_DEFINES = "/usr/cnc/features/system_defines"
_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _system_define_value(host, var):
    """The value of the last `VAR=` line in system_defines ('' if absent)."""
    r = host.run(["sh", "-c",
                  "grep -E '^%s=' %s 2>/dev/null | tail -1" % (var, SYSTEM_DEFINES)])
    line = r.out.strip()
    return line.split("=", 1)[1].strip() if "=" in line else ""


def _required_min(host, p):
    """The minimum a system_define_min check needs: the integer in params
    min_from (e.g. /proc/sys/kernel/msgmnb) if readable and larger, else
    default_min."""
    need = int(p.get("default_min", 0))
    src = p.get("min_from")
    if src:
        r = host.run(["cat", src])
        try:
            need = max(need, int(r.out.strip()))
        except ValueError:
            pass
    return need


def _system_define_min(host, p):
    """An integer variable in system_defines that must be at least a minimum.
    For INC_QUEUE_SIZE the minimum is the kernel's msgmnb: boot_nms
    (test_unix_parms) refuses to boot when INC_QUEUE_SIZE is lower."""
    var = p["variable"]
    need = _required_min(host, p)
    val = _system_define_value(host, var)
    try:
        ok = int(val) >= need
    except ValueError:
        ok = False
    return ok, "{}={} (need at least {})".format(var, val or "(unset)", need)


def _system_define(host, p):
    """A variable in /usr/cnc/features/system_defines whose value must be one of
    expected_values (an absent variable reads as '')."""
    var = p["variable"]
    expected = p.get("expected_values", [])
    val = _system_define_value(host, var)
    return val in expected, "{}={}".format(var, val or "(unset)")


def _sysctl_ok(cur, p):
    """cur meets the check: equal to expected_value, or with compare "min" an
    integer at least expected_value."""
    if p.get("compare") == "min":
        try:
            return int(cur.strip()) >= int(p["expected_value"])
        except ValueError:
            return False
    # Multi-value params (e.g. net.ipv4.tcp_rmem) print tab-separated.
    return " ".join(cur.split()) == " ".join(p["expected_value"].split())


def _kernel_param(host, p):
    """A sysctl parameter that must equal expected_value (or, with compare
    "min", be at least it)."""
    r = host.run(["sysctl", p["param"]])
    if r.rc != 0:
        return False, "cannot read {}".format(p["param"])
    out = r.out.strip()
    cur = out.split(" = ", 1)[1] if " = " in out else out
    want = " (want at least {})".format(p["expected_value"]) if p.get("compare") == "min" else ""
    return _sysctl_ok(cur, p), "{} = {}{}".format(p["param"], cur, want)


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


def _ulimit_head(p):
    return [p["domain"], p["limit_type"], p["limit_item"]]


def _limits_entry(path, head):
    """Value of the last entry for head ([domain, type, item]) in one
    limits-format file, or None."""
    val = None
    for line in (_read_text(path) or "").splitlines():
        parts = line.split()
        if parts and parts[0].startswith("#"):
            continue
        if len(parts) >= 4 and parts[:3] == head:
            val = parts[3]
    return val


def _ulimit_winner(p):
    """(file, value) of the effective (last) entry for p's domain/type/item, or None."""
    head = _ulimit_head(p)
    found = None
    for path in _limits_files():
        val = _limits_entry(path, head)
        if val is not None:
            found = (path, val)
    return found


def _ulimit_expected(p):
    """(value, source) the entry must meet. A check may name `reference` files
    (limits format, e.g. netFLEX's shipped linux-nproc.conf); the first one that
    has the entry wins, so netFLEX stays the single source of the value. Else
    params expected_value (source None); (None, None) if neither."""
    head = _ulimit_head(p)
    for ref in p.get("reference") or []:
        val = _limits_entry(ref, head)
        if val is not None:
            return val, ref
    if p.get("expected_value") is not None:
        return p["expected_value"], None
    return None, None


def _limit_num(v):
    return float("inf") if v in ("unlimited", "infinity", "-1") else int(v)


def _limit_ok(found, expected, compare):
    """compare "min": found must be at least expected (unlimited counts as
    infinite); otherwise the values must match exactly."""
    if compare != "min":
        return found == expected
    try:
        return _limit_num(found) >= _limit_num(expected)
    except ValueError:
        return False


def netflex_present_rcs(host, pkg="netFLEX-CORE", path="/usr/cnc"):
    """(rpm_rc, path_rc) for the two commands that detect an existing netFLEX:
    `rpm -q <pkg>` and a shell test that <path> exists or is a (possibly
    dangling) symlink. Shared with installstate.netflex_installed so both sides
    of the fresh-install precondition agree on exactly what "installed" means."""
    rpm_rc = host.run(["rpm", "-q", pkg]).rc
    path_rc = host.run(["sh", "-c", "test -e {0} || test -L {0}".format(path)]).rc
    return rpm_rc, path_rc


def _netflex_absent(host, p):
    """A fresh-install precondition: neither the netFLEX package nor /usr/cnc
    is present (a dangling symlink left by a previous uninstall still counts),
    and no /usr/cnc_stage is left over (nf_install -i refuses one too, ruling
    R9). The stage path is not part of netflex_present_rcs: a leftover stage
    blocks a fresh install but does not make the host "installed"."""
    pkg = p.get("pkg", "netFLEX-CORE")
    path = p.get("path", "/usr/cnc")
    stage = p.get("stage_path", "/usr/cnc_stage")
    rpm_rc, path_rc = netflex_present_rcs(host, pkg, path)
    if rpm_rc == 0:
        return False, "{} installed".format(pkg)
    if path_rc == 0:
        return False, "{} exists".format(path)
    if host.run(["sh", "-c", "test -e {0} || test -L {0}".format(stage)]).rc == 0:
        return False, "{} exists".format(stage)
    return True, "not installed"


def _mountpoint(host, p):
    path = p["path"]
    ok = host.run(["mountpoint", "-q", path]).rc == 0
    return ok, ("{} mounted".format(path) if ok else "{} is not a mount point".format(path))


def _path_mode(host, p):
    """The permission bits of path match mode. Only the low three octal digits
    are compared, so setuid/setgid/sticky bits (e.g. a setgid /usr4, 2775) do
    not fail a 775 check."""
    path, mode = p["path"], p["mode"]
    found = host.run(["stat", "-c", "%a", path]).out.strip()
    ok = len(found) >= 3 and found[-3:] == str(mode)[-3:]
    return ok, "{} mode {}".format(path, found)


def _ulimit(host, p):
    """A limits.conf / limits.d entry (domain type item value) that must equal
    the expected value, or be at least it with compare "min". Read locally (like
    its fix) from _limits_files()."""
    expected, source = _ulimit_expected(p)
    if expected is None:
        return False, "no reference value ({} not found)".format(
            ", ".join(p.get("reference") or []) or "expected_value")
    want = ""
    if p.get("compare") == "min" or source:
        want = " (want {}{}{})".format("at least " if p.get("compare") == "min" else "",
                                       expected, " from " + source if source else "")
    found = _ulimit_winner(p)
    if found is None:
        return False, "{} {} {} not set{}".format(p["domain"], p["limit_item"],
                                                  p["limit_type"], want)
    return (_limit_ok(found[1], expected, p.get("compare")),
            "{} {} = {}{}".format(p["domain"], p["limit_item"], found[1], want))


KINDS = {
    "rpm_installed": _rpm_installed,
    "file_exists": _file_exists,
    "app_running": _app_running,
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
    "system_define_min": _system_define_min,
    "ulimit": _ulimit,
    "netflex_absent": _netflex_absent,
    "mountpoint": _mountpoint,
    "path_mode": _path_mode,
    "target_newer": _target_newer,
    "rpm_matches_load": _rpm_matches_load,
}

# Kinds that read facts the API supplies (the saved config's target version)
# rather than anything on the box; evaluate() merges facts into their params.
FACT_KINDS = ("target_newer",)


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
    if new_line is not None:            # None: only remove lines
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
    if not _same_file(win[0], SYSCTL_CONF) and not _sysctl_ok(win[1], p):
        return True, "WARNING: set live and persisted in {}, but {} sets {}={} at boot".format(
            SYSCTL_CONF, win[0], p["param"], win[1])
    return True, "{} set and persisted in {}".format(setting, SYSCTL_CONF)


def _ulimit_target(p):
    """The file the fix writes: a check's `target` (e.g. netFLEX's 99-nproc.conf),
    else our own LIMITS_CONF."""
    return p.get("target") or LIMITS_CONF


def _describe_ulimit(p):
    expected, _ = _ulimit_expected(p)
    if expected is None:
        return None
    target = _ulimit_target(p)
    extra = "" if target == LIMITS_CONF else " (removing any copy in {})".format(LIMITS_CONF)
    return "set '{}' in {}{}".format(" ".join(_ulimit_head(p) + [expected]), target, extra)


def _fix_ulimit(host, p):
    expected, _ = _ulimit_expected(p)
    if expected is None:
        return False, "no reference value to set"
    head = _ulimit_head(p)
    entry = " ".join(head + [expected])
    target = _ulimit_target(p)

    def keep(ln):
        parts = ln.split()
        return not (len(parts) >= 4 and parts[:3] == head)
    try:
        _replace_lines(target, keep, entry)
        # One source per entry: drop a copy an earlier fix left in our own file.
        if target != LIMITS_CONF and _limits_entry(LIMITS_CONF, head) is not None:
            _replace_lines(LIMITS_CONF, keep, None)
    except (IOError, OSError, ValueError) as e:
        return False, "could not write {}: {}".format(target, e)
    win = _ulimit_winner(p)
    if win is None:
        return True, "WARNING: written to {} but it is not read by pam_limits".format(target)
    if not _limit_ok(win[1], expected, p.get("compare")):
        return True, "WARNING: written to {} but overridden by {} ({})".format(
            target, win[0], win[1])
    return True, "'{}' written to {}".format(entry, target)


def _fix_path_mode(host, p):
    r = host.run(["chmod", p["mode"], p["path"]])
    if r.rc != 0:
        return False, _cmd_detail(r)
    return True, "{} set to {}".format(p["path"], p["mode"])


def _fix_touchfile(host, p):
    path = "/usr/cnc/features/" + p["file"]
    r = host.run([SCMD, "touch", path], timeout=30)
    if r.rc != 0:
        return False, _cmd_detail(r)
    return True, "{} created".format(path)


def _fix_system_define_min(host, p):
    """Set VAR=<minimum> in system_defines, replacing any existing VAR= lines and
    keeping everything else (comments, order, owner, mode)."""
    var = p["variable"]
    need = _required_min(host, p)
    prefix = var + "="
    try:
        _replace_lines(SYSTEM_DEFINES, lambda ln: not ln.startswith(prefix), "{}{}".format(prefix, need))
    except (IOError, OSError, ValueError) as e:
        return False, "could not update {}: {}".format(SYSTEM_DEFINES, e)
    return True, "{}{} set in {}".format(prefix, need, SYSTEM_DEFINES)


# kind -> (describe(params) -> str, apply(host, params) -> (ok, detail))
FIXES = {
    "rpm_installed": (lambda p: "{} install -y {}".format(_pkg_tool(), p["pkg"]), _fix_rpm),
    "service_active": (lambda p: "systemctl start {0} && systemctl enable {0}".format(p["name"]),
                       _fix_service),
    "kernel_param": (lambda p: "sysctl -w {}={} (persisted in {})".format(
        p["param"], p["expected_value"], SYSCTL_CONF), _fix_kernel_param),
    "ulimit": (_describe_ulimit, _fix_ulimit),
    "touchfile": (lambda p: "{} touch /usr/cnc/features/{}".format(SCMD, p["file"]),
                  _fix_touchfile),
    "path_mode": (lambda p: "chmod {} {}".format(p["mode"], p["path"]), _fix_path_mode),
    "system_define_min": (lambda p: "set {}={}{} in {}".format(
        p["variable"], p.get("default_min", 0),
        " (or {} if larger)".format(p["min_from"]) if p.get("min_from") else "",
        SYSTEM_DEFINES), _fix_system_define_min),
}

# kind -> params -> bool: names that must never reach a root command line.
_FIX_ARGS_OK = {
    "rpm_installed": lambda p: _arg_ok(p["pkg"]),
    "service_active": lambda p: _arg_ok(p["name"]),
    "touchfile": lambda p: _touchfile_name_ok(p["file"]),
    "path_mode": lambda p: p["path"].startswith("/") and re.match(r"^[0-7]{3,4}$", p["mode"]) is not None,
    "system_define_min": lambda p: _VAR_RE.match(p["variable"]) is not None,
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
    if _is_epel(p):
        return None
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
    if _is_epel(p):
        return False, "EPEL packages are not installed by this tool (needs the customer's approval)"
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
    if isinstance(cphase, list):
        if "any" not in cphase and phase not in cphase:
            return False
    elif cphase not in ("any", phase):
        return False
    cmt = check.get("machtype")
    if cmt is not None and cmt != machtype:
        return False
    return True


OUTPUT_CAP = 8000


class _RecordingHost(object):
    """Wraps a Host, running commands unchanged while remembering each
    (argv, RunResult) so a failed check can show what it actually ran."""

    def __init__(self, host):
        self._host = host
        self.runs = []

    def run(self, argv, timeout=None):
        r = self._host.run(argv, timeout=timeout)
        self.runs.append((list(argv), r))
        return r


def _captured_output(runs):
    """The commands a check ran and their output, for display on failure. Each
    block is `$ <argv>` then stdout+stderr. Capped (keeping the tail) so one
    noisy command can't bloat the response."""
    blocks = []
    for argv, r in runs:
        block = "$ " + " ".join(argv)
        body = (r.out or "") + (r.err or "")
        if body.strip():
            block += "\n" + body.rstrip("\n")
        blocks.append(block)
    text = "\n".join(blocks)
    if len(text) > OUTPUT_CAP:
        text = "... (truncated)\n" + text[-OUTPUT_CAP:]
    return text


def evaluate(host, c, when, facts=None):
    """Run one check definition; returns its result dict:
    {name, category, description, severity, passed, detail, blocking, fix, output}.
    output is the captured command output, filled only when the check failed.
    facts (e.g. {"target": {"generic", "load"}}) reach FACT_KINDS only."""
    rec = _RecordingHost(host)
    kind = KINDS.get(c["kind"])
    params = c.get("params", {})
    if c["kind"] in FACT_KINDS:
        params = dict(params, **(facts or {}))
    if kind is None:
        passed, detail = False, "unknown kind: {}".format(c["kind"])
    else:
        try:
            passed, detail = kind(rec, params)
        except Exception as e:                       # a check must never crash the run
            passed, detail = False, "check error: {}".format(e)
    severity = c.get("fail_text", "ERROR")
    return {
        "name": c.get("name", c["kind"]),
        "category": c.get("category", ""),
        "description": c.get("description", ""),
        "severity": severity,
        "passed": passed,
        "detail": detail,
        "blocking": (when == "preflight" and severity == "ERROR" and not passed),
        "fix": None if passed else describe_fix(c),
        "output": "" if passed else _captured_output(rec.runs),
        "hard": bool(c.get("hard", False)),
        "source": c.get("params", {}).get("source"),
    }


def run_checks(host, check_list, when, phase, machtype=None, facts=None):
    """Evaluate every check bound to (when, phase, machtype)."""
    return [evaluate(host, c, when, facts) for c in check_list
            if _applies(c, when, phase, machtype)]


def find_check(check_list, name, when, phase, machtype=None):
    """The definition named `name` that applies to (when, phase, machtype), or None."""
    for c in check_list:
        if c.get("name", c["kind"]) == name and _applies(c, when, phase, machtype):
            return c
    return None


def blocking_failures(results):
    """The subset of results that block a launch."""
    return [r for r in results if r["blocking"]]


def hard_failures(results):
    """The subset of results marked "hard" that failed: these must block an
    install even outside the normal preflight-ERROR blocking rule (e.g. a
    postflight re-check of a hard precondition)."""
    return [r for r in results if r.get("hard") and not r["passed"]]


def install_postflight(host, check_list):
    """Re-run every rpm_installed check bound to (preflight, install) as
    postflight, to confirm packages a fix just installed actually landed.
    Always advisory: a postflight result never blocks."""
    return [evaluate(host, c, "postflight") for c in check_list
            if c.get("kind") == "rpm_installed" and _applies(c, "preflight", "install", None)]
