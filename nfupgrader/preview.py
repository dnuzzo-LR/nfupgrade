"""preview.py — canned install-mode data for NFU_PREVIEW_INSTALL.

Preview lets the install UI be looked at on a box that already runs netFLEX:
the API reports install mode with one of these canned scenarios, and refuses
every action that would change the box (launch, resume, reset, fixes). Nothing
here touches the system."""
from nfupgrader import logtail, progress, steps

SCENARIOS = ("before", "running", "failed", "done")

_STATE = {"before": None, "running": "isrpm", "failed": "ispostinstall", "done": "isdone"}

_INFO = {
    "LOADDIR": "/usr4/inc5.3.002",
    "VERSION": "5.3.0.02",
    "DEPOTFILE": "/opt/apt/upgrade/netFLEX-CORE-5.3.0.02-1.rhel8.x86_64.rpm",
    "LOADDIR_CREATED": "yes",
}

_LOG = {
    "before": [],
    "running": [
        "2026-10-22 09:00:01 INFO  Starting install of 5.3.0.02 into /usr4/inc5.3.002 (preview)",
        "2026-10-22 09:00:02 INFO  Created /usr4/inc5.3.002 and the /usr/cnc, /usr/cnc_stage links",
        "2026-10-22 09:00:03 INFO  Installing netFLEX-CORE-5.3.0.02-1.rhel8.x86_64.rpm",
    ],
    "failed": [
        "2026-10-22 09:00:01 INFO  Starting install of 5.3.0.02 into /usr4/inc5.3.002 (preview)",
        "2026-10-22 09:03:10 INFO  netFLEX-CORE installed",
        "2026-10-22 09:05:44 INFO  Running post-install.sh",
        "2026-10-22 09:05:51 EXEC  [postinstall] cannot create /usr/cnc/data/example: No such file",
        "2026-10-22 09:05:51 WARN  [postinstall] exited 1",
        "2026-10-22 09:05:51 ERROR post-install.sh failed (preview sample)",
        "2026-10-22 09:05:51 FATAL Aborting install at step ispostinstall",
    ],
    "done": [
        "2026-10-22 09:00:01 INFO  Starting install of 5.3.0.02 into /usr4/inc5.3.002 (preview)",
        "2026-10-22 09:03:10 INFO  netFLEX-CORE installed",
        "2026-10-22 09:09:30 WARN  web GUI: cpjsmap.osm not found - skipped (preview sample)",
        "2026-10-22 09:09:31 INFO  license setup not yet implemented - skipped",
        "2026-10-22 09:09:31 INFO  Install complete",
    ],
}

TRACE = "(preview - no trace file)"


def scenario_from_env(value):
    """The scenario named by NFU_PREVIEW_INSTALL: None when unset/empty (preview
    off), the named scenario, or 'before' for any other value (e.g. '1')."""
    if not value:
        return None
    return value if value in SCENARIOS else "before"


def log_entries(scenario, min_severity=None):
    """Parsed canned trace lines for the scenario, filtered like logtail."""
    entries = [logtail.parse_line(ln) for ln in _LOG[scenario]]
    if not min_severity:
        return entries
    return [e for e in entries if logtail._passes(e, min_severity)]


def status(scenario, hostname):
    """A /api/install/status body for the scenario."""
    state = _STATE[scenario]
    return {
        "mode": "install",
        "installed": scenario in ("failed", "done"),
        "running": scenario == "running",
        "unfinished": scenario in ("running", "failed"),
        "state": state,
        "label": steps.label(state) if state else "",
        "info": dict(_INFO) if state else {},
        "trace": TRACE,
        "fatal": [e["msg"] for e in log_entries(scenario, "ERROR")],
        "preview": scenario,
    }


def progress_record(scenario):
    """A /api/progress body (phase install) for the scenario."""
    state = _STATE[scenario]
    if state is None:
        return {"phase": "install", "current": None, "label": "", "completed": 0,
                "step": None, "total": steps.total("install"), "status": "not_started"}
    return progress.compute_progress("install", state, scenario == "running")
