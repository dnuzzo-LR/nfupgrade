"""localinfo.py — gather this host's read-only facts through the Host.run seam.
Pure parsers turn command output into structure; gather() composes them. The
same collectors serve peers over ssh in Phase 4b."""
import json
import re

from nfupgrader import site


def parse_incinfo(out):
    """incinfo may emit JSON (a record object) or human text depending on the
    box. Return the parsed dict when it is a JSON object, else the raw string."""
    out = out.strip()
    try:
        v = json.loads(out)
        if isinstance(v, dict):
            return v
    except ValueError:
        pass
    return out


def parse_rpm_version(out):
    """From `rpm -qi <pkg>` output return 'Version-Release', or None if absent."""
    ver = rel = None
    for line in out.splitlines():
        if line.startswith("Version"):
            ver = line.split(":", 1)[1].strip()
        elif line.startswith("Release"):
            rel = line.split(":", 1)[1].strip()
    if not ver:
        return None
    return "{}-{}".format(ver, rel) if rel else ver


def parse_df(out, keep=None):
    """Parse `df -kP` output into [{mount, use, avail_kb}]. If keep is given
    (an iterable of mount points), return only those."""
    rows = []
    lines = out.splitlines()
    for line in lines[1:]:               # skip header
        parts = line.split()
        if len(parts) < 6:
            continue
        mount = parts[5]
        if keep is not None and mount not in keep:
            continue
        try:
            avail_kb = int(parts[3])
        except ValueError:
            avail_kb = None
        rows.append({"mount": mount, "use": parts[4], "avail_kb": avail_kb})
    return rows


#: dbcheck's clean summary line (netFLEX cnc/tools/src/dbcheck.c).
_DBCHECK_CLEAN = re.compile(r"db audit\(s\) complete, 0 errors found")
#: dbcheck's summary-table total: "  Total<tabs><errors found><tabs><errors corrected>".
_DBCHECK_TOTAL = re.compile(r"^\s*Total\s+(\d+)\s+\d+\s*$", re.M)


def parse_dbcheck_errors(out, rc):
    """Number of database errors from `dbcheck -AV` output and exit code, or
    None when it cannot be told (dbcheck failed before its summary, or the
    summary and exit code disagree). Uses dbcheck's own summary, never a count
    of lines mentioning "error": the clean summary itself says "0 errors found",
    and each error appears both as an "Error N:" line and in the per-type table.
    dbcheck exits 0 only when its total is 0."""
    clean = _DBCHECK_CLEAN.search(out or "")
    total = _DBCHECK_TOTAL.search(out or "")
    if clean and not total:
        n = 0
    elif total and not clean:
        n = int(total.group(1))
    else:
        return None
    if (rc == 0) != (n == 0):
        return None
    return n


_DISK_KEEP = ("/usr4", "/usr2", "/tmp", "/var")


def _dbcheck_errors(host):
    r = host.run(["dbcheck", "-AV"])
    return parse_dbcheck_errors(r.out, r.rc)


def _readlink(host, path):
    r = host.run(["readlink", path])
    return r.out.strip() if r.rc == 0 and r.out.strip() else None


def _rpm(host, pkg):
    return parse_rpm_version(host.run(["rpm", "-qi", pkg]).out)


def _rpm_records(host, pkg):
    """Every installed record of pkg as Version-Release, in rpm's order."""
    r = host.run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}\\n", pkg])
    if r.rc != 0:
        return []
    return [ln.strip() for ln in r.out.splitlines() if ln.strip()]


def _generic(record):
    """'5.4.1.28-1' -> '5.4.1' (the first three components of the Version)."""
    return ".".join(record.split("-", 1)[0].split(".")[:3])


def version_notes(host):
    """Flags for the Overview's CORE/PATCH/IPATCH rows (#10): more than one
    netFLEX-CORE record, or a PATCH/IPATCH of another generic than the running
    CORE (the last record, as shown and as nf_upgrade reads it) -- left by an
    upgrade, which never removes the old release's records. None when fine.
    Nothing is hidden or removed."""
    notes = {"core": None, "patch": None, "ipatch": None}
    core = _rpm_records(host, "netFLEX-CORE")
    if not core:
        return notes
    if len(core) > 1:
        notes["core"] = ("{} netFLEX-CORE records: {} (a leftover? see the rpm-matches-load check)"
                         .format(len(core), ", ".join(core)))
    gen = _generic(core[-1])
    for key, pkg in (("patch", "netFLEX-CORE-PATCH"), ("ipatch", "netFLEX-CORE-IPATCH")):
        stale = sorted(set(_generic(r) for r in _rpm_records(host, pkg)) - {gen})
        if stale:
            notes[key] = "left from {}: not part of the running {} release".format(
                ", ".join(stale), gen)
    return notes


def _test_f(host, path):
    return host.run(["test", "-f", path]).rc == 0


#: Process names netFLEX's boot menu counts as "Controller is running"
#: (3b2/shell/boot_nms rewrites .CNC_UP from exactly this ps check).
APP_PROCS = frozenset(["AT", "sdi", "dacs", "screen", "arm", "bill", "dump"])
#: Only processes run by this user count, so e.g. a support user's GNU screen
#: session cannot make a stopped netFLEX look up.
APP_USER = "cmm"
APP_PS_ARGV = ["ps", "-e", "-o", "user=,comm="]


def app_state(host, incroot="/usr/cnc"):
    """Whether netFLEX is running: "up", "down" or "stale".

    "up" needs both <incroot>/.CNC_UP and a cmm-owned APP_PROCS process.
    "stale" is .CNC_UP with no such process: left behind by a forced stop or
    an interrupted boot; boot_nms itself clears it the next time it runs, and
    this never touches it. If ps cannot be run, the flag is trusted ("up")."""
    if host.run(["test", "-f", incroot + "/.CNC_UP"]).rc != 0:
        return "down"
    r = host.run(APP_PS_ARGV)
    if r.rc != 0:
        return "up"
    for ln in r.out.splitlines():
        f = ln.split()
        if len(f) == 2 and f[0] == APP_USER and f[1] in APP_PROCS:
            return "up"
    return "stale"


def gather(host, hostname, cnc_cnfg_path="/usr/cnc/features/cnc.cnfg", tier1_only=False):
    """Collect this host's read-only facts. Tier-2 (app-dependent) fields are
    gathered only when netFLEX is up (app_state) and tier1_only is False (peers
    are tier-1 only)."""
    try:
        with open(cnc_cnfg_path) as fh:
            hosts = site.parse_cnfg(fh.read())
    except (IOError, OSError):
        hosts = []
    me = site.local_host(hosts, hostname)
    topology = {
        "kind": "multibox" if len(hosts) > 1 else "singlebox",
        "role": me.role if me else None,
        "mate": (me.mate if me and me.mate != me.name else None),
        "host_count": len(hosts),
    }

    state = app_state(host)
    app_up = state == "up"

    gr_status = host.run(["cat", "/usr/cnc/.GR_STATUS"])
    gr = {
        "transfer": gr_status.out.strip() if gr_status.rc == 0 and gr_status.out.strip() else "none",
        "restore": _test_f(host, "/usr/cnc/grestore.pid"),
    }

    info = {
        "host": hostname,
        "topology": topology,
        "versions": {
            "core": _rpm(host, "netFLEX-CORE"),
            "patch": _rpm(host, "netFLEX-CORE-PATCH"),
            "ipatch": _rpm(host, "netFLEX-CORE-IPATCH"),
            "notes": version_notes(host),
        },
        "loads": {
            "installed": _readlink(host, "/usr/cnc"),
            "staged": _readlink(host, "/usr/cnc_stage"),
            "saved": _readlink(host, "/usr/cnc_saved"),
        },
        "app_up": app_up,
        "app_state": state,
        "gr": gr,
        "disk": parse_df(host.run(["df", "-kP"]).out, keep=_DISK_KEEP),
        "app_detail": None,
        "links": None,
    }

    if app_up and not tier1_only:
        info["app_detail"] = {
            "incinfo": parse_incinfo(host.run(["/usr/cnc/bin/incinfo", "-j"]).out),
            "rdb_version": host.run(["rdb", "version"]).out.strip(),
            "dbcheck_errors": _dbcheck_errors(host),
        }
        info["links"] = host.run(["/usr/cnc/mbin/nestat", "-B"]).out.strip()

    return info
