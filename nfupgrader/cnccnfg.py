"""cnccnfg.py -- render /usr/cnc/features/cnc.cnfg for a new install and read
/etc/hosts for the host-picker on the install form.

Header text is verbatim from nf-install's singlebox.cnc.cnfg / multibox.cnc.cnfg
(machine names in those files are examples only). The machine-line format and
field order match nfupgrader.site.parse_cnfg, which this module round-trips
with.
"""
import re

#: Box types the install form may choose from.
BOX_TYPES = ("SINGLE", "FEP", "BEP")

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_SINGLE_HEADER = (
    "# SingleBox (MUST be MultiBox or Single)\n"
    "# SystemPrompt=netFLEX\n"
    "# SystemString=INTEGRATED NETWORK CONTROLLER\n"
    "# Master Machine Name\n"
)

_MULTI_HEADER = (
    "# MultiBox (MUST be MultiBox or Single)\n"
    "# SystemPrompt=netFLEX\n"
    "# SystemString=INTEGRATED NETWORK CONTROLLER\n"
    "# Master Machine Name\n"
)

_MACH_HEADER = "# Mach Mach-# Mate Mate-# Mach-Type (CNC=1 Mux=0) Override-Link (CNC)\n"


def valid_hostname(name):
    """@brief Check whether name is a syntactically valid cnc.cnfg host name.

    @param name  Candidate host name.
    @return True if name is non-empty and matches ^[A-Za-z0-9][A-Za-z0-9._-]*$.
    """
    return bool(name) and bool(_HOSTNAME_RE.match(name))


def render_cnc_cnfg(boxtype, thishost, mate=None, master=None):
    """@brief Render the text of cnc.cnfg for a SINGLE/FEP/BEP install.

    For FEP/BEP, the FEP machine line is always emitted before the BEP line,
    regardless of which of thishost/mate is the FEP.

    @param boxtype   One of BOX_TYPES.
    @param thishost  Name of the host this cnc.cnfg is being written for.
    @param mate      Name of the mate host; required for FEP/BEP, must differ
                      from thishost.
    @param master    Name of the master machine; required for FEP/BEP, must be
                      thishost or mate.
    @return The full cnc.cnfg text, trailing newline included.
    @raises ValueError  Unknown boxtype, invalid host name, missing/self mate,
                         or master not in {thishost, mate}.
    """
    if boxtype not in BOX_TYPES:
        raise ValueError("unknown box type: {0}".format(boxtype))
    if not valid_hostname(thishost):
        raise ValueError("invalid host name: {0!r}".format(thishost))

    if boxtype == "SINGLE":
        return _SINGLE_HEADER + thishost + "\n" + _MACH_HEADER

    if not valid_hostname(mate):
        raise ValueError("invalid or missing mate host name: {0!r}".format(mate))
    if mate == thishost:
        raise ValueError("mate must differ from this host: {0!r}".format(mate))
    if master not in (thishost, mate):
        raise ValueError("master must be this host or mate: {0!r}".format(master))

    fep, bep = (thishost, mate) if boxtype == "FEP" else (mate, thishost)
    body = (
        _MULTI_HEADER + master + "\n" + _MACH_HEADER +
        "{0} 1 {1} 1 1 9\n".format(fep, bep) +
        "{0} 1 {1} 1 0 0\n".format(bep, fep)
    )
    return body


def render_from_values(values, thishost):
    """@brief Render cnc.cnfg from install-form config values.

    @param values    Mapping with BOXTYPE, MATEHOST, MASTERHOST keys; values
                      are stripped of surrounding whitespace before use.
    @param thishost  Name of the host this cnc.cnfg is being written for.
    @return The full cnc.cnfg text, trailing newline included.
    @raises ValueError  See render_cnc_cnfg.
    """
    def _strip(key):
        val = values.get(key)
        if val is None:
            return None
        val = val.strip()
        return val if val else None

    boxtype = _strip("BOXTYPE")
    mate = _strip("MATEHOST")
    master = _strip("MASTERHOST")
    return render_cnc_cnfg(boxtype, thishost, mate=mate, master=master)


def parse_etc_hosts(text):
    """@brief Parse /etc/hosts text into an ordered name/ip list.

    Blank lines, whole-line comments, and trailing "# ..." comments are
    skipped. Loopback entries (IPs starting "127." or "::1") are skipped.
    Every name/alias on a kept line is yielded in order; the first occurrence
    of a given name wins over any later duplicate.

    @param text  Contents of an /etc/hosts file.
    @return List of {"name": str, "ip": str} dicts, in file order.
    """
    seen = set()
    entries = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        ip, names = fields[0], fields[1:]
        if ip.startswith("127.") or ip.startswith("::1"):
            continue
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            entries.append({"name": name, "ip": ip})
    return entries


def read_etc_hosts(path="/etc/hosts"):
    """@brief Read and parse an /etc/hosts file.

    @param path  Path to the hosts file.
    @return List of {"name", "ip"} dicts as from parse_etc_hosts, or []
            if the file cannot be read.
    """
    try:
        with open(path) as fh:
            text = fh.read()
    except (IOError, OSError):
        return []
    return parse_etc_hosts(text)
