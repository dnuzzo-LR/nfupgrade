"""bootmenu.py -- the Boot menu tab's backend (runs netFLEX's new_boot_nms
menu as user cmm on a PTY).

The pure, testable pieces: the exact command line to run, reading the process
table from /proc, and classifying boot-menu processes the tool did not itself
start (a foreign live login vs. a leftover from a previous run of this tool,
detected via a pidfile). Plus BootMenuSession, which spawns the command on a
PTY and owns it: scrollback, one attached browser at a time, and a targeted
end().
"""
import codecs
import errno
import fcntl
import os
import select
import signal
import struct
import termios
import threading
import time

from nfupgrader.launcher import _SHELLS, is_script_argv

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
#: menu session is running. Content: "<tool_pid> <session_pid> <session_starttime>\n"
#: (starttime is the session pid's /proc/<pid>/stat field 22, used to detect
#: pid reuse: see classify_others).
PIDFILE_NAME = "nfupgrader_bootmenu.pid"


def default_argv():
    """Return the argv used to launch the boot menu session as user cmm.

    `su` is run with `-s /bin/sh -c SESSION_SCRIPT` rather than invoking the
    menu directly, since `su` requires a shell to run the command in; that
    shell then `exec`s into the menu so it is never left on the process tree.
    """
    return ["su", "cmm", "-s", "/bin/sh", "-c", SESSION_SCRIPT]


def _parse_stat(text):
    """Return (pid, ppid, starttime) parsed from a /proc/<pid>/stat line. The
    process name field is "(name)" and may itself contain spaces or
    parentheses, so the fields after it are taken from the text after the
    LAST ')'. That remainder starts at field 3 (state), so ppid is
    remainder[1] (field 4) and starttime -- field 22 -- is remainder[19].
    starttime is 0 if the line is too short to have that field."""
    pid_str, _, rest = text.partition("(")
    close = rest.rfind(")")
    fields = rest[close + 1:].split()
    # fields[0] is state, fields[1] is ppid, fields[19] is starttime
    pid = int(pid_str.strip())
    ppid = int(fields[1])
    try:
        starttime = int(fields[19])
    except (IndexError, ValueError):
        starttime = 0
    return pid, ppid, starttime


def list_processes(proc_root="/proc"):
    """Read the process table from proc_root (default "/proc").

    Returns a list of {"pid": int, "ppid": int, "argv": [str], "starttime":
    int}. Entries that are not numeric pid directories, or whose
    stat/cmdline vanished or are unreadable (the process exited mid-scan),
    are silently skipped. starttime is 0 if it could not be parsed.
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
            pid, ppid, starttime = _parse_stat(stat_text)
        except (ValueError, IndexError):
            continue
        argv = [a for a in cmdline_raw.decode("utf-8", "replace").split("\0") if a]
        procs.append({"pid": pid, "ppid": ppid, "argv": argv, "starttime": starttime})
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
    """True iff pid names a live process, probed with a signal-0 kill. EPERM
    (we can't signal it) still means it exists; only ESRCH means it's dead."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def read_pidfile(pidfile_path):
    """Return (tool_pid, session_pid, session_starttime) from pidfile_path,
    or None if it does not exist or is not exactly 3 integer fields (which
    includes the old 2-field format -- callers must treat that as "no usable
    pidfile", not as a leftover)."""
    try:
        with open(pidfile_path, "r") as fh:
            text = fh.read()
    except (IOError, OSError):
        return None
    parts = text.split()
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def proc_starttime(pid, proc_root="/proc"):
    """Return pid's starttime (/proc/<pid>/stat field 22), or 0 if it can't
    be read (no such process, unreadable, malformed)."""
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), "r") as fh:
            return _parse_stat(fh.read())[2]
    except (IOError, OSError, ValueError, IndexError):
        return 0


def write_pidfile(path, tool_pid, session_pid, proc_root="/proc", starttime=None):
    """Atomically write the pidfile: "<tool_pid> <session_pid>
    <session_starttime>\n" (written to path + ".tmp", fsync'd, then renamed,
    so a reader never sees a partial file). session_starttime is starttime
    if given, else read from proc_root for session_pid (0 if the process
    can't be found/read there, e.g. it already exited)."""
    if starttime is None:
        starttime = proc_starttime(session_pid, proc_root)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("{} {} {}\n".format(tool_pid, session_pid, starttime))
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)
    return path


def classify_others(procs, own_pids, pidfile_path, pid_alive=None):
    """Classify boot-menu processes in procs that are not in own_pids.

    Returns (kind, pids): pids is the sorted list of menu pids not in
    own_pids; kind is:
      - "none"     if there are no such pids;
      - "leftover" if the pidfile has exactly 3 integer fields, its tool pid
                   is not alive (per pid_alive, default os.kill(pid,
                   0)-based), the recorded session pid is present in procs
                   with a matching starttime (guards against the session pid
                   having been reused by an unrelated, later process), and
                   every such menu pid is that session pid or a descendant
                   of it;
      - "foreign"  otherwise (including a missing/malformed/2-field pidfile,
                   a live tool pid, a vanished session pid, or a starttime
                   mismatch).
    """
    if pid_alive is None:
        pid_alive = _pid_alive_default

    others = sorted(
        p["pid"] for p in procs if is_menu_argv(p["argv"]) and p["pid"] not in own_pids
    )
    if not others:
        return "none", []

    parsed = read_pidfile(pidfile_path)
    if parsed is not None:
        tool_pid, session_pid, recorded_starttime = parsed
        if not pid_alive(tool_pid):
            session_proc = next((p for p in procs if p["pid"] == session_pid), None)
            if session_proc is not None and session_proc["starttime"] == recorded_starttime:
                allowed = {session_pid} | descendants(procs, session_pid)
                if all(pid in allowed for pid in others):
                    return "leftover", others

    return "foreign", others


def proc_name(argv):
    """The name a process goes by for BootMenuSession.end()/busy(): the
    basename of argv[0] or, for a shell running a script (`<shell> [-opts]
    path ...`, as for a #! script), the script's basename. A shell running
    `-c` or nothing is just the shell. Mirrors launcher.is_script_argv.
    "" for an empty argv (zombie, kernel thread)."""
    if not argv:
        return ""
    names = [os.path.basename(a) for a in argv]
    if names[0] in _SHELLS:
        for arg, name in zip(argv[1:], names[1:]):
            if arg.startswith("-"):
                if arg == "-c":
                    break
                continue
            return name
    return names[0]


def _exit_code(status):
    """Shell-style exit code from a waitpid status: the exit status, or
    128 + signal number if killed by a signal."""
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)


#: Seconds the reader thread waits in select() before re-checking whether
#: the child exited (the PTY need not reach EOF: an orphan the menu started
#: may still hold the slave open).
_POLL = 0.1
#: Max seconds write() waits for the PTY to accept input.
_WRITE_TIMEOUT = 5.0
#: Max seconds end() waits for the child to be reaped after killing it.
_END_TIMEOUT = 5.0


class BootMenuSession(object):
    """The boot menu running on a PTY, owned by this tool process.

    Locking: self._lock (an RLock, with self._cond on it) guards all session
    state and the attachment; frames are sent to the attached client while it
    is held so a reattach's replay can never interleave with live output.
    self._fd_lock guards the master fd's lifetime for write()/resize() (which
    must not hold self._lock while blocking on the PTY, or the reader could
    not drain output and the child could deadlock); it is always taken before
    self._lock, never after. Only the reader thread reads, reaps the child
    and closes the fd.
    """

    def __init__(self, argv=None, env=None, scrollback=262144, pidfile=None,
                 kill_names=None):
        self.argv = list(argv) if argv is not None else default_argv()
        if env is None:
            env = dict(os.environ)
            env["TERM"] = "xterm-256color"
        self.env = env
        self.scrollback_limit = scrollback
        self.pidfile = pidfile
        self.kill_names = (set(kill_names) if kill_names is not None
                           else {"su"} | set(MENU_SCRIPTS))
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._fd_lock = threading.Lock()
        self._state = "none"
        self._pid = None
        self._fd = None
        self._started_at = None
        self._exit_code = None
        self._buf = bytearray()
        self._client = None          # (token, send) or None
        self._next_token = 0
        self._thread = None

    # -- lifecycle -----------------------------------------------------

    def start(self, cols=80, rows=24):
        """Spawn argv on a new PTY (child is session leader, PTY its
        controlling tty, as pty.fork() does), sized cols x rows; start the reader thread and
        write the pidfile. Returns the child pid. RuntimeError if running."""
        with self._lock:
            if self._state == "running":
                raise RuntimeError("boot menu session already running")
            # openpty + fork (what pty.fork does) rather than pty.fork, so the
            # size is set before the child exists: neither the child's first
            # size query nor an early resize() can race with it.
            fd, slave = os.openpty()
            try:
                fcntl.ioctl(slave, termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0))
                pid = os.fork()
            except BaseException:
                os.close(fd)
                os.close(slave)
                raise
            if pid == 0:  # child: exec or die, never return into Python
                try:
                    os.close(fd)
                    os.setsid()
                    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                    for target in (0, 1, 2):
                        os.dup2(slave, target)
                    if slave > 2:
                        os.close(slave)
                    # Undo inherited ignores (the tool ignores SIGHUP; Python
                    # ignores SIGPIPE) so the menu behaves as in a login.
                    for sig in (signal.SIGHUP, signal.SIGPIPE, signal.SIGXFSZ):
                        signal.signal(sig, signal.SIG_DFL)
                    os.execvpe(self.argv[0], self.argv, self.env)
                except BaseException as exc:
                    try:
                        os.write(2, "exec {} failed: {}\r\n".format(
                            self.argv[0], exc).encode("utf-8", "replace"))
                    finally:
                        os._exit(127)
            os.close(slave)
            os.set_inheritable(fd, False)
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self._pid, self._fd = pid, fd
            self._state = "running"
            self._started_at = time.time()
            self._exit_code = None
            self._buf = bytearray()
            if self.pidfile:
                self._write_pidfile(pid)
            # Started last, under the lock: finalisation (which removes the
            # pidfile) can't run before the pidfile is written.
            self._thread = threading.Thread(target=self._reader, args=(pid, fd),
                                            name="bootmenu-reader")
            self._thread.daemon = True
            self._thread.start()
            self._send_locked(self._status_frame())
            return pid

    def _write_pidfile(self, pid):
        """Write the pidfile for pid, retrying briefly if /proc does not yet
        show a starttime. Failure to write is not fatal to the session."""
        starttime = 0
        deadline = time.time() + 0.5
        while True:
            starttime = proc_starttime(pid)
            if starttime or time.time() >= deadline:
                break
            time.sleep(0.01)
        try:
            write_pidfile(self.pidfile, os.getpid(), pid, starttime=starttime)
        except (IOError, OSError):
            pass

    def state(self):
        """{"state": "none"|"running"|"ended", "pid", "started_at",
        "exit_code"}."""
        with self._lock:
            return {"state": self._state, "pid": self._pid,
                    "started_at": self._started_at, "exit_code": self._exit_code}

    def running(self):
        with self._lock:
            return self._state == "running"

    def _procs_and_pid(self):
        """(process table, child pid) or (None, None) if not running."""
        with self._lock:
            if self._state != "running":
                return None, None
            pid = self._pid
        return list_processes(), pid

    def own_pids(self):
        """The child pid and all its descendants (from /proc); empty set if
        not running."""
        procs, pid = self._procs_and_pid()
        if procs is None:
            return set()
        return {pid} | descendants(procs, pid)

    def busy(self):
        """True iff some descendant of the child is neither a kill_names
        process nor a bare shell -- i.e. the menu is running work."""
        procs, pid = self._procs_and_pid()
        if procs is None:
            return False
        desc = descendants(procs, pid)
        for p in procs:
            if p["pid"] in desc:
                name = proc_name(p["argv"])
                if name and name not in self.kill_names and name not in _SHELLS:
                    return True
        return False

    def end(self):
        """SIGKILL each session process (child or descendant, read fresh
        from /proc) whose proc_name is in kill_names -- individually, never
        the process group, never anything else -- then wait for the child
        to be reaped. Returns True iff something was killed."""
        procs, pid = self._procs_and_pid()
        if procs is None:
            return False
        targets = {pid} | descendants(procs, pid)
        killed = False
        for p in procs:
            if p["pid"] in targets and proc_name(p["argv"]) in self.kill_names:
                try:
                    os.kill(p["pid"], signal.SIGKILL)
                    killed = True
                except OSError:
                    pass
        if killed:
            deadline = time.time() + _END_TIMEOUT
            with self._lock:
                while self._state == "running" and self._pid == pid:
                    left = deadline - time.time()
                    if left <= 0:
                        break
                    self._cond.wait(left)
        return killed

    # -- attachment ----------------------------------------------------

    def attach(self, send):
        """Make send(dict) the attached client, replacing (and telling) any
        previous one. send first gets the scrollback replay (if any), then
        the status. Returns a token for detach()."""
        with self._lock:
            if self._client is not None:
                old = self._client[1]
                self._client = None
                try:
                    old({"type": "detached", "reason": "opened elsewhere"})
                except Exception:
                    pass
            self._next_token += 1
            token = self._next_token
            self._client = (token, send)
            replay = self.scrollback()
            if replay:
                self._send_locked({"type": "output", "data": replay, "replay": True})
            self._send_locked(self._status_frame())
            return token

    def detach(self, token):
        """Detach the client with this token; no-op if it is not current."""
        with self._lock:
            if self._client is not None and self._client[0] == token:
                self._client = None

    def _status_frame(self):
        frame = {"type": "status", "state": self._state}
        if self._state == "ended":
            frame["exit_code"] = self._exit_code
        return frame

    def _send_locked(self, frame):
        """Send frame to the attached client (caller holds self._lock); a
        send that raises detaches that client."""
        client = self._client
        if client is None:
            return
        try:
            client[1](frame)
        except Exception:
            if self._client is client:
                self._client = None

    # -- I/O -----------------------------------------------------------

    def write(self, data):
        """Write data (str, UTF-8) to the PTY. RuntimeError if not running
        or if the PTY does not accept it within _WRITE_TIMEOUT."""
        payload = data.encode("utf-8")
        with self._fd_lock:
            with self._lock:
                if self._state != "running" or self._fd is None:
                    raise RuntimeError("boot menu session not running")
                fd = self._fd
            deadline = time.time() + _WRITE_TIMEOUT
            view = memoryview(payload)
            while view:
                try:
                    n = os.write(fd, view)
                    view = view[n:]
                    continue
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                        raise RuntimeError("boot menu write failed: {}".format(exc))
                left = deadline - time.time()
                if left <= 0:
                    raise RuntimeError("boot menu not accepting input")
                select.select([], [fd], [], min(left, _POLL))

    def resize(self, cols, rows):
        """Set the PTY window size (the kernel SIGWINCHes the foreground
        process group). No-op if not running."""
        with self._fd_lock:
            with self._lock:
                if self._state != "running" or self._fd is None:
                    return
                fd = self._fd
            fcntl.ioctl(fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", int(rows), int(cols), 0, 0))

    def scrollback(self):
        """The last `scrollback` bytes of output, decoded (errors replaced).
        Leading UTF-8 continuation bytes cut by the trim are skipped."""
        with self._lock:
            buf = bytes(self._buf)
        i = 0
        while i < len(buf) and i < 3 and 0x80 <= buf[i] <= 0xBF:
            i += 1
        return buf[i:].decode("utf-8", "replace")

    # -- reader thread -------------------------------------------------

    def _reader(self, pid, fd):
        """Read output until the child exits; then finalise. EOF on the
        master is not required (an orphan may hold the slave open), so the
        child is also polled with WNOHANG."""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        status = None
        eof = False
        try:
            while status is None:
                if not eof:
                    try:
                        r, _, _ = select.select([fd], [], [], _POLL)
                    except (OSError, ValueError):
                        r, eof = [], True
                    if r:
                        eof = not self._read_once(fd, decoder)
                if eof:
                    _, status = os.waitpid(pid, 0)
                    break
                wpid, st = os.waitpid(pid, os.WNOHANG)
                if wpid:
                    status = st
                    # Drain what the child wrote before exiting (bounded, in
                    # case an orphan keeps writing).
                    for _ in range(256):
                        r, _, _ = select.select([fd], [], [], 0)
                        if not r or not self._read_once(fd, decoder):
                            break
        except Exception:
            if status is None:
                try:
                    _, status = os.waitpid(pid, 0)
                except OSError:
                    status = 0
        self._finalise(pid, fd, status, decoder)

    def _read_once(self, fd, decoder):
        """Read one chunk from fd and record/forward it. False on EOF/EIO."""
        try:
            data = os.read(fd, 65536)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                return True
            return False
        if not data:
            return False
        with self._lock:
            self._buf.extend(data)
            excess = len(self._buf) - self.scrollback_limit
            if excess > 0:
                del self._buf[:excess]
            text = decoder.decode(data)
            if text:
                self._send_locked({"type": "output", "data": text})
        return True

    def _finalise(self, pid, fd, status, decoder):
        """Child reaped: close the master, remove the pidfile, mark ended,
        tell the client, wake end()."""
        with self._fd_lock:
            with self._lock:
                tail = decoder.decode(b"", final=True)
                if tail:
                    self._send_locked({"type": "output", "data": tail})
                try:
                    os.close(fd)
                except OSError:
                    pass
                self._fd = None
                if self.pidfile:
                    try:
                        os.unlink(self.pidfile)
                    except OSError:
                        pass
                self._state = "ended"
                self._exit_code = _exit_code(status) if status is not None else None
                self._send_locked({"type": "exit", "code": self._exit_code})
                self._cond.notify_all()
