"""steps.py — the upgrade state machine as data, mirroring nf_upgrader's
stage_new_sw / upgrade_new_sw loops. Drives the progress record."""

_STAGE = ["swsstart", "swspatchinstall", "swswebinstall",
          "swscopycnc", "swsretropass1", "swscomplete"]

_UPGRADE = ["swscomplete", "swistart", "swicopycnc", "swipackages",
            "swipreboot", "swiretropass2", "swibootinc", "postinstall", "swidone"]

_STATES = {"stage": _STAGE, "upgrade": _UPGRADE}

_LABELS = {
    "swsstart":        "Preliminary checks and CORE install",
    "swspatchinstall": "Install patch filesets",
    "swswebinstall":   "Install web GUI",
    "swscopycnc":      "Copy CORE files (pass 1)",
    "swsretropass1":   "Database retrofit (pass 1 of 3)",
    "swscomplete":     "Staging complete",
    "swistart":        "Stop application, begin upgrade",
    "swicopycnc":      "Copy CORE files (pass 2), switch links",
    "swipackages":     "Linux package updates",
    "swipreboot":      "Pre-boot preparation",
    "swiretropass2":   "Database retrofit (pass 2 of 3)",
    "swibootinc":      "Boot the application",
    "postinstall":     "Post-install steps",
    "swidone":         "Upgrade complete",
    "swscoreupgrade":  "Install CORE RPM",  # retained for label lookups
}


def states_for(phase):
    """Ordered list of states for 'stage' or 'upgrade'. KeyError if unknown."""
    return list(_STATES[phase])


def total(phase):
    """Number of states in the phase."""
    return len(_STATES[phase])


def index_of(phase, state):
    """Zero-based index of state within the phase, or -1 if not present."""
    try:
        return _STATES[phase].index(state)
    except ValueError:
        return -1


def label(state):
    """Human-readable description for a state name, or the name itself."""
    return _LABELS.get(state, state)
