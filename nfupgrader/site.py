"""site.py — parse /usr/cnc/features/cnc.cnfg into a host inventory and identify
the local host. Vendored logic from nf-install's multibox_config.py.

A machine record is 6 space-separated fields:
    name  number  mate  mate_number  type  flags
where type 1 = CNC/FEP, 0 = MUX/BEP."""
import collections

HostInfo = collections.namedtuple(
    "HostInfo", ["name", "number", "mate", "mate_number", "role"]
)


def parse_cnfg(text):
    """Return a list of HostInfo from cnc.cnfg text, skipping comments/blanks."""
    hosts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 6:
            continue
        role = "FEP" if parts[4] == "1" else "BEP"
        hosts.append(HostInfo(parts[0], parts[1], parts[2], parts[3], role))
    return hosts


def local_host(hosts, hostname):
    """Return the HostInfo whose name matches hostname, or None."""
    for h in hosts:
        if h.name == hostname:
            return h
    return None


def read_site(path="/usr/cnc/features/cnc.cnfg"):
    """Read and parse cnc.cnfg from disk; [] if the file is absent."""
    try:
        with open(path) as fh:
            return parse_cnfg(fh.read())
    except (IOError, OSError):
        return []
