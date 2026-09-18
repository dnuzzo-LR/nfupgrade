"""host.py — the single execution seam. Every command the service runs goes
through a Host, so local vs remote lives in one place and tests use FakeHost."""
import collections
import subprocess

RunResult = collections.namedtuple("RunResult", ["rc", "out", "err"])


class Host(object):
    """Runs commands on the local machine (Phase 2). ssh support arrives in Phase 4."""

    def __init__(self, name="localhost"):
        self.name = name
        self.local = True

    def run(self, argv, timeout=None):
        """Run argv (a list), capture output, return a RunResult. Never raises on
        non-zero exit; a missing binary yields rc 127."""
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
