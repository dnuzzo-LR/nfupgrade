"""host.py — the single execution seam. Every command the service runs goes
through a Host (local) or SshHost (peer, read-only), so local vs remote lives in
one place and tests use FakeHost."""
import collections
import shlex
import subprocess

RunResult = collections.namedtuple("RunResult", ["rc", "out", "err"])


def _exec(argv, timeout=None):
    """Run argv locally, capture output, return a RunResult. Never raises on
    non-zero exit; a missing binary yields rc 127, a timeout rc 124."""
    try:
        p = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
        )
        return RunResult(p.returncode, p.stdout, p.stderr)
    except FileNotFoundError:
        return RunResult(127, "", "not found: {}".format(argv[0]))
    except subprocess.TimeoutExpired:
        return RunResult(124, "", "timeout: {}".format(" ".join(argv)))


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

    def run(self, argv, timeout=None):
        """Run argv (a list) locally; return a RunResult."""
        return _exec(argv, timeout=timeout)


class SshHost(object):
    """Runs commands on a peer over ssh, same interface as Host. Read-only use."""

    def __init__(self, name, connect_timeout=5):
        self.name = name
        self.local = False
        self.connect_timeout = connect_timeout

    def run(self, argv, timeout=None):
        return _exec(build_ssh_argv(self.name, argv, self.connect_timeout), timeout=timeout)


class FakeHost(object):
    """Test double. Maps tuple(argv) -> RunResult; unknown argv -> rc 1."""

    def __init__(self, responses=None):
        self.name = "fake"
        self.local = True
        self.responses = responses or {}
        self.calls = []

    def run(self, argv, timeout=None):
        self.calls.append(list(argv))
        return self.responses.get(tuple(argv), RunResult(1, "", "unmocked"))
