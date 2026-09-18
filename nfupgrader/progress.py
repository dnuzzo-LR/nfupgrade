"""progress.py — re-derive the local upgrade progress record on every read.

The service only observes: it reads .${HOST}_STATE and a liveness boolean
(is an nf_upgrader process alive) and never remembers state itself."""
import os
from nfupgrader import steps

_TERMINAL = {"stage": "swscomplete", "upgrade": "swidone"}


def compute_progress(phase, current, running):
    """Pure: map a phase + current state + liveness to a progress dict.

    status is one of: done, running, died. (not_started is decided by the
    reader when there is no state file.)"""
    total = steps.total(phase)
    idx = steps.index_of(phase, current)
    completed = idx if idx >= 0 else 0
    if current == _TERMINAL[phase]:
        completed = total
        status = "done"
    elif running:
        status = "running"
    else:
        status = "died"
    return {
        "phase": phase,
        "current": current,
        "label": steps.label(current),
        "completed": completed,
        "total": total,
        "status": status,
    }


def read_progress(hostname, phase, inclogdir, running):
    """Read .${hostname}_STATE from inclogdir and compute progress. If the state
    file is absent, the phase has not started."""
    path = os.path.join(inclogdir, ".{}_STATE".format(hostname))
    if not os.path.exists(path):
        return {
            "phase": phase, "current": None, "label": "",
            "completed": 0, "total": steps.total(phase), "status": "not_started",
        }
    with open(path) as fh:
        current = fh.read().strip()
    return compute_progress(phase, current, running)
