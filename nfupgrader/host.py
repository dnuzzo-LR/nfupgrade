"""host.py — the single execution seam. Every command the service runs goes
through a Host (local) or SshHost (peer, read-only), so local vs remote lives in
one place and tests use FakeHost."""
import collections
import os
import shlex
import signal
import subprocess

RunResult = collections.namedtuple("RunResult", ["rc", "out", "err"])


def _exec(argv, timeout=None, input=None):
    """Run argv locally, capture output, return a RunResult. Never raises on
    non-zero exit; a missing binary yields rc 127, a timeout rc 124.
    `input` (str) is written to the child's stdin then closed; None means
    stdin is /dev/null.

    The child runs in its own session/process group so a timeout can kill the
    whole pipeline: killing only `sh` would leave grandchildren (e.g. the
    `sleep` in `sh -c 'sleep 6 | cat'`) holding the pipes open, and we would
    block until they exit. Side effect: a Ctrl-C to the service no longer
    reaches these children -- acceptable, they are bounded by `timeout`."""
    try:
        p = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError:
        return RunResult(127, "", "not found: {}".format(argv[0]))
    try:
        out, err = p.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # something escaped the group and holds the pipe; give up on output
        return RunResult(124, "", "timeout: {}".format(" ".join(argv)))
    return RunResult(p.returncode, out, err)


def build_ssh_argv(name, argv, connect_timeout=5):
    """Build an ssh argv that runs `argv` on host `name` non-interactively.
    The remote command is a single shell-quoted string."""
    remote = " ".join(shlex.quote(a) for a in argv)
    return ["ssh", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout={}".format(connect_timeout),
            name, remote]


class Host(object):
    """Runs commands on the local machine."""

    def __init__(self, name="localhost"):
        self.name = name
        self.local = True

    def run(self, argv, timeout=None, input=None):
        """Run argv (a list) locally; return a RunResult. `input` goes to stdin."""
        return _exec(argv, timeout=timeout, input=input)


class SshHost(object):
    """Runs commands on a peer over ssh, same interface as Host. Read-only use."""

    def __init__(self, name, connect_timeout=5):
        self.name = name
        self.local = False
        self.connect_timeout = connect_timeout

    def run(self, argv, timeout=None, input=None):
        return _exec(build_ssh_argv(self.name, argv, self.connect_timeout),
                     timeout=timeout, input=input)


class FakeHost(object):
    """Test double. Maps tuple(argv) -> RunResult; unknown argv -> rc 1."""

    def __init__(self, responses=None):
        self.name = "fake"
        self.local = True
        self.responses = responses or {}
        self.calls = []
        self.inputs = []

    def run(self, argv, timeout=None, input=None):
        """Record the call; a response may be a callable f(argv, input)."""
        self.calls.append(list(argv))
        self.inputs.append(input)
        r = self.responses.get(tuple(argv), RunResult(1, "", "unmocked"))
        if callable(r):
            return r(list(argv), input)
        return r
