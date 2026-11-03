"""bootmenu.py -- command, /proc discovery, and pidfile classification for the
Boot menu tab (runs netFLEX's new_boot_nms menu as user cmm on a PTY).

This module holds only the pure, testable pieces: the exact command line to
run, reading the process table from /proc, and classifying boot-menu
processes the tool did not itself start (a foreign live login vs. a leftover
from a previous run of this tool, detected via a pidfile). The PTY session
class that actually spawns and drives the command lives elsewhere.
"""
import os

from nfupgrader.launcher import is_script_argv

#: Script basenames that count as "the boot menu" for classification.
MENU_SCRIPTS = ("new_boot_nms", "boot_nms")

#: Mirrors cmm_.profile. `exec` replaces the shell with the menu so that when
#: the menu exits, the session ends -- the browser must never land on a shell
#: prompt.
SESSION_SCRIPT = (
    "export SHELL=/usr/bin/ksh EDITOR=vi PATH=/usr/cnc/scmds-bin:/usr/bin:$PATH; "
    "umask 000; stty erase '^?' echoe 2>/dev/null; exec /usr/cnc/bin/new_boot_nms"
)

#: Name of the pidfile nfupgrader writes (under its INCLOGDIR) while a boot
#: menu session is running. Content: "<tool_pid> <session_pid>\n".
PIDFILE_NAME = "nfupgrader_bootmenu.pid"


def default_argv():
    """Return the argv used to launch the boot menu session as user cmm.

    `su` is run with `-s /bin/sh -c SESSION_SCRIPT` rather than invoking the
    menu directly, since `su` requires a shell to run the command in; that
    shell then `exec`s into the menu so it is never left on the process tree.
    """
    return ["su", "cmm", "-s", "/bin/sh", "-c", SESSION_SCRIPT]


def _parse_stat(text):
    """Return (pid, ppid) parsed from a /proc/<pid>/stat line. The process
    name field is "(name)" and may itself contain spaces or parentheses, so
    the ppid is taken from the text after the LAST ')'."""
    pid_str, _, rest = text.partition("(")
    close = rest.rfind(")")
    fields = rest[close + 1:].split()
    # fields[0] is state, fields[1] is ppid
    return int(pid_str.strip()), int(fields[1])


def list_processes(proc_root="/proc"):
    """Read the process table from proc_root (default "/proc").

    Returns a list of {"pid": int, "ppid": int, "argv": [str]}. Entries that
    are not numeric pid directories, or whose stat/cmdline vanished or are
    unreadable (the process exited mid-scan), are silently skipped.
    """
    procs = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return procs
    for name in entries:
        if not name.isdigit():
            continue
        pid_dir = os.path.join(proc_root, name)
        try:
            with open(os.path.join(pid_dir, "stat"), "r") as fh:
                stat_text = fh.read()
            with open(os.path.join(pid_dir, "cmdline"), "rb") as fh:
                cmdline_raw = fh.read()
        except (IOError, OSError):
            continue
        try:
            pid, ppid = _parse_stat(stat_text)
        except (ValueError, IndexError):
            continue
        argv = [a for a in cmdline_raw.decode("utf-8", "replace").split("\0") if a]
        procs.append({"pid": pid, "ppid": ppid, "argv": argv})
    return procs


def is_menu_argv(argv):
    """True iff argv runs one of the boot menu scripts (see
    launcher.is_script_argv for exactly what counts)."""
    return any(is_script_argv(argv, script) for script in MENU_SCRIPTS)


def descendants(procs, pid):
    """Return the set of pids in procs descended from pid, excluding pid
    itself."""
    children = {}
    for p in procs:
        children.setdefault(p["ppid"], []).append(p["pid"])
    result = set()
    frontier = [pid]
    while frontier:
        current = frontier.pop()
        for child in children.get(current, []):
            if child not in result:
                result.add(child)
                frontier.append(child)
    return result


def _pid_alive_default(pid):
    """True iff pid names a live process, probed with a signal-0 kill."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pidfile(pidfile_path):
    """Return (tool_pid, session_pid) from pidfile_path, or None if it does
    not exist or is malformed."""
    try:
        with open(pidfile_path, "r") as fh:
            text = fh.read()
    except (IOError, OSError):
        return None
    parts = text.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def classify_others(procs, own_pids, pidfile_path, pid_alive=None):
    """Classify boot-menu processes in procs that are not in own_pids.

    Returns (kind, pids): pids is the sorted list of menu pids not in
    own_pids; kind is:
      - "none"     if there are no such pids;
      - "leftover" if the pidfile exists, its tool pid is not alive
                   (per pid_alive, default os.kill(pid, 0)-based), and every
                   such menu pid is the pidfile's session pid or a descendant
                   of it;
      - "foreign"  otherwise.
    """
    if pid_alive is None:
        pid_alive = _pid_alive_default

    others = sorted(
        p["pid"] for p in procs if is_menu_argv(p["argv"]) and p["pid"] not in own_pids
    )
    if not others:
        return "none", []

    parsed = _read_pidfile(pidfile_path)
    if parsed is not None:
        tool_pid, session_pid = parsed
        if not pid_alive(tool_pid):
            allowed = {session_pid} | descendants(procs, session_pid)
            if all(pid in allowed for pid in others):
                return "leftover", others

    return "foreign", others
