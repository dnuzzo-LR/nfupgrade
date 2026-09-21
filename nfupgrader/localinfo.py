"""localinfo.py — gather this host's read-only facts through the Host.run seam.
Pure parsers turn command output into structure; gather() composes them. The
same collectors serve peers over ssh in Phase 4b."""
import json

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


def parse_dbcheck_errors(out):
    """Count ERROR lines in dbcheck -AV output."""
    return sum(1 for ln in out.splitlines() if "ERROR" in ln.upper())


_DISK_KEEP = ("/usr4", "/usr2", "/tmp", "/var")


def _readlink(host, path):
    r = host.run(["readlink", path])
    return r.out.strip() if r.rc == 0 and r.out.strip() else None


def _rpm(host, pkg):
    return parse_rpm_version(host.run(["rpm", "-qi", pkg]).out)


def _test_f(host, path):
    return host.run(["test", "-f", path]).rc == 0


def gather(host, hostname, cnc_cnfg_path="/usr/cnc/features/cnc.cnfg", tier1_only=False):
    """Collect this host's read-only facts. Tier-2 (app-dependent) fields are
    gathered only when /usr/cnc/.CNC_UP exists and tier1_only is False (peers
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

    app_up = _test_f(host, "/usr/cnc/.CNC_UP")

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
        },
        "loads": {
            "installed": _readlink(host, "/usr/cnc"),
            "staged": _readlink(host, "/usr/cnc_stage"),
            "saved": _readlink(host, "/usr/cnc_saved"),
        },
        "app_up": app_up,
        "gr": gr,
        "disk": parse_df(host.run(["df", "-kP"]).out, keep=_DISK_KEEP),
        "app_detail": None,
        "links": None,
    }

    if app_up and not tier1_only:
        info["app_detail"] = {
            "incinfo": parse_incinfo(host.run(["/usr/cnc/bin/incinfo"]).out),
            "rdb_version": host.run(["rdb", "version"]).out.strip(),
            "dbcheck_errors": parse_dbcheck_errors(host.run(["dbcheck", "-AV"]).out),
        }
        info["links"] = host.run(["/usr/cnc/mbin/nestat", "-B"]).out.strip()

    return info
