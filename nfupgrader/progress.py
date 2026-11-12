"""progress.py — re-derive the local upgrade progress record on every read.

The service only observes: it reads .${HOST}_STATE and a liveness boolean
(is an nf_upgrader process alive) and never remembers state itself."""
import os
from nfupgrader import steps

_TERMINAL = {"stage": "swscomplete", "upgrade": "swidone", "install": "isdone"}


def compute_progress(phase, current, running):
    """Pure: map a phase + current state + liveness to a progress dict.

    completed counts finished states (drives the bar); step is the 1-based
    number of the state in progress (total once done, None when not started or
    the state is unknown).

    status is one of: done, running, not_started, died, failed (the state
    file says "<state> failed": failed_state names that step). In the upgrade phase a
    box still at the stage terminal state with nothing running has simply not
    started the upgrade yet. (A missing state file is not_started too; that is
    decided by the reader.)"""
    total = steps.total(phase)
    failed_state = failed_step(current)
    idx = steps.index_of(phase, failed_state or current)
    completed = idx if idx >= 0 else 0
    if failed_state:
        # fail_upgrade wrote "<state> failed" and the engine exited.
        status = "failed"
    elif current == _TERMINAL[phase]:
        completed = total
        status = "done"
    elif running:
        status = "running"
    elif phase == "upgrade" and current == _TERMINAL["stage"]:
        status = "not_started"
    else:
        status = "died"
    if status == "done":
        step = total
    elif status == "not_started" or idx < 0:
        step = None
    else:
        step = idx + 1
    return {
        "phase": phase,
        "current": current,
        "label": steps.label(failed_state or current),
        "completed": completed,
        "step": step,
        "total": total,
        "status": status,
        "failed_state": failed_state,
    }


FAILED_SUFFIX = " failed"


def failed_step(current):
    """The step a "<state> failed" state file names (nf_upgrader's
    fail_upgrade), else None."""
    if current and current.endswith(FAILED_SUFFIX):
        return current[:-len(FAILED_SUFFIX)].strip() or None
    return None


# States that only occur once the upgrade phase has begun.
_UPGRADE_ONLY = set(steps.states_for("upgrade")) - set(steps.states_for("stage"))


def state_file(hostname, phase="stage"):
    """The state file basename for hostname/phase: .{h}_INSTALL_STATE for the
    install phase, else .{h}_STATE (shared by stage and upgrade, per nf_upgrader)."""
    if phase == "install":
        return ".{}_INSTALL_STATE".format(hostname)
    return ".{}_STATE".format(hostname)


def read_state(hostname, inclogdir, phase="stage"):
    """The current state from the phase's state file, or None if absent/empty."""
    try:
        with open(os.path.join(inclogdir, state_file(hostname, phase))) as fh:
            return fh.read().strip() or None
    except (IOError, OSError):
        return None


def phase_done(hostname, inclogdir, phase):
    """True iff the phase's state file is at that phase's terminal state
    (e.g. swidone for upgrade): the phase has run to completion on this host."""
    return read_state(hostname, inclogdir, phase) == _TERMINAL[phase]


def infer_phase(current):
    """'upgrade' once the state machine is past staging, else 'stage'."""
    return "upgrade" if (failed_step(current) or current) in _UPGRADE_ONLY else "stage"


def read_progress(hostname, phase, inclogdir, running):
    """Read the phase's state file from inclogdir and compute progress. If the
    state file is absent, the phase has not started."""
    current = read_state(hostname, inclogdir, phase)
    if current is None:
        return {
            "phase": phase, "current": None, "label": "",
            "completed": 0, "step": None, "total": steps.total(phase), "status": "not_started",
            "failed_state": None,
        }
    return compute_progress(phase, current, running)
