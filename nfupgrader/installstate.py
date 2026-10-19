"""installstate.py — read nf_install's state/info files and decide whether a
host is mid-install, versus already upgraded (or being upgraded in the normal
stage/upgrade flow).

nf_install writes `.${HOST}_INSTALL_STATE` (the step to run next; a fatal step
leaves the state at that step, never advancing to isdone) and
`.${HOST}_INSTALL_INFO` (KEY=VALUE, e.g. LOADDIR/VERSION/DEPOTFILE). It never
writes `.${HOST}_STATE` (that file is nf_upgrader's resume point)."""
import os

from nfupgrader import cfg
from nfupgrader import checks
from nfupgrader import progress
from nfupgrader import steps


def read_install_info(hostname, logdir):
    """KEY=VALUE pairs from .${hostname}_INSTALL_INFO, or {} if absent."""
    try:
        with open(os.path.join(logdir, ".{}_INSTALL_INFO".format(hostname))) as fh:
            text = fh.read()
    except (IOError, OSError):
        return {}
    return cfg.parse_cfg(text)


def unfinished(hostname, logdir):
    """True if an install state file exists, is non-empty, and has not reached
    the install terminal state (isdone)."""
    current = progress.read_state(hostname, logdir, "install")
    return current is not None and current != steps.states_for("install")[-1]


def netflex_installed(host):
    """True if netFLEX is already present on host: the same `rpm -q
    netFLEX-CORE` / /usr/cnc existence check as checks.py's netflex_absent
    kind, inverted."""
    rpm_rc, path_rc = checks.netflex_present_rcs(host)
    return rpm_rc == 0 or path_rc == 0


def host_mode(host, hostname, logdir):
    """'install' if an install is unfinished, or netFLEX is not yet installed;
    else 'upgrade'."""
    if unfinished(hostname, logdir) or not netflex_installed(host):
        return "install"
    return "upgrade"
