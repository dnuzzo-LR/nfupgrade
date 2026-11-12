"""summary.py — what an Upgrade launch will do, said before Launch (#9).

Read-only. Everything comes from the box (through the Host seam), the saved
config, and the staged load; each piece is None when it cannot be read. The
facts mirror nf_upgrader: stop_inc kill -9s every process whose command is
USER (`ps -e | grep USER | while read pid b c proc`), doswistart backs up the
RDB and boots at the end only when netFLEX was up and RESTARTINC != NO. The
PostgreSQL upgrade is not in nf_upgrader: the new load's bin/dbupgrade.sh
runs it, with UPGRADE_VERSION as its target."""
import re

from nfupgrader import checks

STAGE_DBUPGRADE = "/usr/cnc_stage/bin/dbupgrade.sh"
_UPGRADE_VERSION = re.compile(r"^\s*UPGRADE_VERSION=[\"']?(\d+)", re.M)


def _out(host, argv):
    r = host.run(argv)
    return r.out if r.rc == 0 else None


def _from_version(host):
    v = checks.upgrade_from_version(host)
    return "{}.{}".format(*v) if v else None


def _to_version(values):
    g, l = (values.get("NEW_GENERIC") or "").strip(), (values.get("NEW_LOAD") or "").strip()
    return "{}.{}".format(g, l) if g and l else None


def _users(host):
    out = _out(host, ["ps", "-e"])
    if out is None:
        return None
    n = 0
    for ln in out.splitlines():
        f = ln.split(None, 3)
        if len(f) == 4 and f[3].strip() == "USER":
            n += 1
    return n


def _rdb_data_dir(host):
    return checks.rdb_data_dir(host)


def _rdb_gb(host, pgdata):
    if not pgdata:
        return None
    out = _out(host, ["du", "-sk", pgdata])
    try:
        return round(int(out.split()[0]) / 1048576.0, 1)
    except (AttributeError, IndexError, ValueError):
        return None


def _pg(host, pgdata):
    cur = None
    if pgdata:
        out = _out(host, ["cat", pgdata + "/PG_VERSION"])
        cur = out.strip() if out and out.strip().isdigit() else None
    m = _UPGRADE_VERSION.search(_out(host, ["cat", STAGE_DBUPGRADE]) or "")
    target = m.group(1) if m else None
    upgrade = int(target) > int(cur) if cur and target else None
    return {"current": cur, "target": target, "upgrade": upgrade}


def _boot(host, values):
    if (values.get("RESTARTINC") or "").strip().upper() == "NO":
        return {"will_boot": False, "why": "RESTARTINC=NO"}
    up = any(host.run(["test", "-f", "/usr/cnc/" + f]).rc == 0 for f in (".CNC_UP", ".MUX_UP"))
    if not up:
        return {"will_boot": False, "why": "netFLEX is not up now"}
    return {"will_boot": True, "why": "netFLEX is up now and RESTARTINC is not NO"}


def upgrade_summary(host, values):
    """{from, to, users, rdb_gb, pg: {current, target, upgrade}, boot:
    {will_boot, why}} for an Upgrade launched now with config `values`."""
    pgdata = _rdb_data_dir(host)
    return {
        "from": _from_version(host),
        "to": _to_version(values),
        "users": _users(host),
        "rdb_gb": _rdb_gb(host, pgdata),
        "pg": _pg(host, pgdata),
        "boot": _boot(host, values),
    }
