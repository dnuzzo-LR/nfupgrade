"""logtail.py — parse trace lines into structured entries and tail the file
incrementally with an optional minimum-severity filter."""
import os
import re

# Severities the fork emits, plus EXEC for sub-command output. Ordered for
# min-severity filtering; EXEC is treated as below WARN (it is child output,
# not a severity) so a WARN+ filter hides it.
_ORDER = {"EXEC": 0, "INFO": 1, "WARN": 2, "ERROR": 3, "FATAL": 4}
_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"(?P<sev>INFO|WARN|ERROR|FATAL|EXEC)\s{1,2}(?P<msg>.*)$"
)


def parse_line(line):
    """Return {ts, severity, msg} for a trace line. Unmatched lines (e.g. raw
    child output) are classified INFO with ts None."""
    m = _LINE.match(line.rstrip("\n"))
    if m:
        return {"ts": m.group("ts"), "severity": m.group("sev"), "msg": m.group("msg")}
    return {"ts": None, "severity": "INFO", "msg": line.rstrip("\n")}


def _passes(entry, min_severity):
    if not min_severity:
        return True
    return _ORDER.get(entry["severity"], 1) >= _ORDER.get(min_severity, 1)


def tail(path, min_severity=None, limit=None):
    """Return a list of parsed entries from the whole file, optionally filtered
    by minimum severity and truncated to the last `limit` entries."""
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except (IOError, OSError):
        return []
    entries = [parse_line(ln) for ln in lines]
    entries = [e for e in entries if _passes(e, min_severity)]
    if limit is not None:
        entries = entries[-limit:]
    return entries


def latest_trace(inclogdir, phase, hostname):
    """The newest nf_upgrader trace for this phase/host. nf_upgrader writes
    ${INCLOGDIR}/${MODE}_inc_${HOSTNAME}.${PID} (plus .web/.rpm/... side files,
    which are ignored); with none present, fall back to the pid-less name."""
    pat = re.compile(r"^{}_inc_{}\.\d+$".format(re.escape(phase), re.escape(hostname)))
    best, best_key = None, None
    try:
        names = os.listdir(inclogdir)
    except OSError:
        names = []
    for name in names:
        if not pat.match(name):
            continue
        path = os.path.join(inclogdir, name)
        try:
            key = (os.path.getmtime(path), name)
        except OSError:
            continue
        if best_key is None or key > best_key:
            best, best_key = path, key
    return best or os.path.join(inclogdir, "{}_inc_{}".format(phase, hostname))


def latest_of(inclogdir, phases, hostname):
    """The newest existing `<phase>_inc_<host>.<pid>` trace across several
    phases (e.g. nf_install's "install" and "reset" traces share a host's
    install history). Falls back to latest_trace(inclogdir, phases[0],
    hostname) -- which in turn falls back to the pid-less path -- when none
    of the phases have a trace on disk."""
    pats = [(phase, re.compile(r"^{}_inc_{}\.\d+$".format(re.escape(phase), re.escape(hostname))))
            for phase in phases]
    best, best_key = None, None
    try:
        names = os.listdir(inclogdir)
    except OSError:
        names = []
    for name in names:
        if not any(pat.match(name) for _, pat in pats):
            continue
        path = os.path.join(inclogdir, name)
        try:
            key = (os.path.getmtime(path), name)
        except OSError:
            continue
        if best_key is None or key > best_key:
            best, best_key = path, key
    return best or latest_trace(inclogdir, phases[0], hostname)


def tail_since(path, offset, min_severity=None):
    """Incremental tail: read from byte `offset`, return new entries and the new
    offset. Lets the UI poll for only what was appended."""
    try:
        size = os.path.getsize(path)
    except (IOError, OSError):
        return {"entries": [], "next_offset": 0}
    if offset > size:      # file rotated/truncated -> restart
        offset = 0
    with open(path) as fh:
        fh.seek(offset)
        data = fh.read()
        new_offset = fh.tell()
    entries = [parse_line(ln) for ln in data.splitlines()]
    entries = [e for e in entries if _passes(e, min_severity)]
    return {"entries": entries, "next_offset": new_offset}
