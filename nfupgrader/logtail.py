"""logtail.py — parse trace lines into structured entries and tail the file
incrementally with an optional minimum-severity filter."""
import os
import re
import time

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


def terminal_lines(text):
    """Split text into lines as a terminal shows them: on newlines only, each
    keeping the text after its last carriage return. Progress bars (zstd, rpm)
    redraw with \r, and splitting on \r too flooded the Log tab (#8)."""
    out = []
    for ln in text.split("\n"):
        ln = ln.rstrip("\r")
        out.append(ln.rsplit("\r", 1)[-1])
    if out and out[-1] == "":
        out.pop()
    return out


def _passes(entry, min_severity):
    if not min_severity:
        return True
    return _ORDER.get(entry["severity"], 1) >= _ORDER.get(min_severity, 1)


def tail(path, min_severity=None, limit=None):
    """Return a list of parsed entries from the whole file, optionally filtered
    by minimum severity and truncated to the last `limit` entries."""
    try:
        # newline="": keep \r (text mode would turn each into a line break)
        with open(path, newline="") as fh:
            lines = terminal_lines(fh.read())
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
    with open(path, newline="") as fh:
        fh.seek(offset)
        data = fh.read()
        new_offset = fh.tell()
    entries = [parse_line(ln) for ln in terminal_lines(data)]
    entries = [e for e in entries if _passes(e, min_severity)]
    return {"entries": entries, "next_offset": new_offset}


def live_step(logdir, max_lines=40):
    """(path, last lines) of the step output run_logged is buffering right now.

    run_logged (nf_log.ksh) captures each sub-command in ${INCLOGDIR}/.nfrun.<pid>
    and only copies it into the trace when the command ends, so a long step
    (linux_packages, the database setup) is silent in the trace until then. The
    newest such buffer is the step in progress. (None, []) if there is none."""
    best, best_key = None, None
    try:
        names = os.listdir(logdir)
    except OSError:
        return None, []
    for name in names:
        if not name.startswith(".nfrun."):
            continue
        path = os.path.join(logdir, name)
        try:
            key = (os.path.getmtime(path), name)
        except OSError:
            continue
        if best_key is None or key > best_key:
            best, best_key = path, key
    if best is None:
        return None, []
    lines = _tail_text(best, max_lines)
    return (None, []) if lines is None else (best, lines)


LIVE_DIRS = ("/usr/cnc/logs",)


def _tail_text(path, max_lines):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            data = fh.read().decode("utf-8", "replace")
    except (IOError, OSError):
        return None
    return terminal_lines(data)[-max_lines:]


def live_log(logdir, trace, extra_dirs=LIVE_DIRS, max_lines=40, now=None):
    """{file, lines, age} of the newest log written since the main trace's last
    line, while an engine runs (#8). Long steps write elsewhere: run_logged's
    .nfrun.<pid> buffer, the trace's own sub-logs (<trace>.copy_cnc, .rpm,
    .rpm_patch, .web, <trace>ATTEXT.rpm), or netFLEX's /usr/cnc/logs. Only
    top-level regular files; age is seconds since it was written."""
    empty = {"file": None, "lines": [], "age": None}
    try:
        floor = os.path.getmtime(trace) if trace else 0
    except OSError:
        floor = 0
    base = os.path.basename(trace) if trace else None
    cands = []
    try:
        for name in os.listdir(logdir):
            if name.startswith(".nfrun.") or (base and name != base and name.startswith(base)
                                               and name[len(base):][:1] in (".", "A")):
                cands.append(os.path.join(logdir, name))
    except OSError:
        pass
    for d in extra_dirs:
        try:
            cands.extend(os.path.join(d, n) for n in os.listdir(d))
        except OSError:
            continue
    best, best_m = None, None
    for c in cands:
        try:
            if not os.path.isfile(c):
                continue
            m = os.path.getmtime(c)
        except OSError:
            continue
        live = m > floor or os.path.basename(c).startswith(".nfrun.")
        if live and (best_m is None or m > best_m):
            best, best_m = c, m
    if best is None:
        return empty
    lines = _tail_text(best, max_lines)
    if lines is None:
        return empty
    now = time.time() if now is None else now
    return {"file": best, "lines": lines, "age": max(0, int(now - best_m))}
