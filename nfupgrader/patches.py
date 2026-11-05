"""patches.py — pure eligibility rules for netFLEX-CORE-PATCH RPMs.

No I/O: callers gather installed state and per-file rpm metadata and pass
them to evaluate(), which decides which single patch may be installed next
(strict sequence) and labels every other file with why it cannot be."""
import re

from nfupgrader import cfg

#: The only package name this tool will install.
SUPPORTED_PKG = "netFLEX-CORE-PATCH"

_REBOOT_RE = re.compile(r'^\s*REBOOT_LOADS="([^"]*)"', re.M)


def split_version(version):
    """Split 'X.Y.Z.LL' into (generic, int load): '5.4.1.32' -> ('5.4.1', 32).
    Returns None when the version has fewer than 4 dotted parts or the load
    is not all digits."""
    if not version:
        return None
    generic, load = cfg.derive_generic_load(version)
    if not load or not load.isdigit():
        return None
    return generic, int(load)


def parse_reboot_loads(scripts_out):
    """Return the ints in the first REBOOT_LOADS="..." line of `rpm -qp
    --scripts` output, or [] when absent or empty."""
    m = _REBOOT_RE.search(scripts_out or "")
    if not m:
        return []
    return [int(t) for t in m.group(1).split() if t.isdigit()]


def reboot_required(reboot_loads, current_load, install_load):
    """The scriptlet's own rule: a reboot-load applies when the current load
    is below it or the load being installed equals it."""
    for rl in reboot_loads:
        if current_load < rl or install_load == rl:
            return True
    return False


def current_load(installed):
    """Current load: the PATCH load if its generic matches CORE's, else the
    CORE load. None when netFLEX-CORE is not installed."""
    core = split_version(installed.get("core"))
    if core is None:
        return None
    patch = split_version(installed.get("patch"))
    if patch is not None and patch[0] == core[0]:
        return patch[1]
    return core[1]


def evaluate(installed, files, host_block, app_up):
    """Label each file dict with "status", "reason" and "needs_stop".

    installed: {"core", "core_arch", "patch"} (version strings or None).
    files: dicts with path, name, pkg, version, arch, reboot_loads.
    host_block: reason string blocking the whole host, or None.
    app_up: True when netFLEX is running.
    Statuses: installable, installed, out_of_sequence, wrong_release,
    wrong_arch, unsupported, unreadable, blocked."""
    core = split_version(installed.get("core"))
    cur = current_load(installed)
    patch = split_version(installed.get("patch"))
    patch_active = core is not None and patch is not None and patch[0] == core[0]

    rows = []
    cands = []
    for src in files:
        row = dict(src)
        row["needs_stop"] = False
        row["reason"] = ""
        rows.append(row)
        ver = split_version(row.get("version"))
        if row.get("pkg") is None or ver is None:
            row["status"], row["reason"] = "unreadable", "rpm could not read this file"
        elif row["pkg"] != SUPPORTED_PKG:
            row["status"], row["reason"] = "unsupported", "not a netFLEX-CORE-PATCH package"
        elif core is None:
            row["status"], row["reason"] = "blocked", "netFLEX-CORE is not installed"
        elif ver[0] != core[0]:
            row["status"] = "wrong_release"
            row["reason"] = "release {0} does not match installed {1}".format(ver[0], core[0])
        elif row.get("arch") != installed.get("core_arch"):
            row["status"] = "wrong_arch"
            row["reason"] = "arch {0} does not match installed {1}".format(
                row.get("arch"), installed.get("core_arch"))
        elif ver[1] <= cur:
            row["status"], row["reason"] = "installed", "already installed (load {0})".format(cur)
        else:
            row["_load"] = ver[1]
            cands.append(row)

    if cands:
        if patch_active:
            want = cur + 1
        else:
            want = min(r["_load"] for r in cands)
        first = None
        for row in cands:
            if row["_load"] == want and first is None:
                first = row
                row["status"] = "installable"
                row["reason"] = ""
            elif row["_load"] == want:
                row["status"] = "out_of_sequence"
                row["reason"] = "duplicate of {0}".format(first["name"])
            else:
                row["status"] = "out_of_sequence"
                row["reason"] = "install {0}.{1} first".format(core[0], want)
        if first is not None:
            if host_block:
                first["status"], first["reason"] = "blocked", host_block
            elif app_up and reboot_required(first.get("reboot_loads") or [], cur, want):
                first["needs_stop"] = True
    for row in rows:
        row.pop("_load", None)
    return rows

