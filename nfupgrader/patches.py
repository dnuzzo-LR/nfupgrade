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



def _highest(versions):
    """Highest of several version strings: by (int load, string)."""
    def key(v):
        sp = split_version(v)
        return (sp[1] if sp else -1, v)
    return max(versions, key=key) if versions else None


def _read_installed(host):
    """Query rpm for installed CORE/PATCH versions. Returns the installed dict
    (core, core_arch, patch, current_load) and whether an IPATCH is present."""
    core = core_arch = None
    r = host.run(["rpm", "-q", "--qf", "%{VERSION} %{ARCH}\\n", "netFLEX-CORE"])
    if r.rc == 0:
        pairs = [ln.split() for ln in r.out.splitlines() if len(ln.split()) >= 2]
        if pairs:
            best = max(pairs, key=lambda t: (
                (split_version(t[0]) or ("", -1))[1], t[0]))
            core, core_arch = best[0], best[1]
    patch = None
    r = host.run(["rpm", "-q", "--qf", "%{VERSION}\\n", "netFLEX-CORE-PATCH"])
    if r.rc == 0:
        patch = _highest([ln.strip() for ln in r.out.splitlines() if ln.strip()])
    ipatch = host.run(["rpm", "-q", "netFLEX-CORE-IPATCH"]).rc == 0
    installed = {"core": core, "core_arch": core_arch, "patch": patch}
    installed["current_load"] = current_load(installed)
    return installed, ipatch


def _read_file(host, path):
    """Describe one patch rpm file as an evaluate() input dict."""
    row = {"path": path, "name": path.rsplit("/", 1)[-1], "pkg": None,
           "version": None, "arch": None, "reboot_loads": []}
    r = host.run(["rpm", "-qp", "--qf", "%{NAME} %{VERSION} %{ARCH}\\n", path])
    if r.rc == 0:
        parts = r.out.split()
        if len(parts) >= 3:
            row["pkg"], row["version"], row["arch"] = parts[0], parts[1], parts[2]
    return row


def gather(host, dirs, stage_root="/usr/cnc_stage", incroot="/usr/cnc"):
    """Collect installed state and depot patch files, then evaluate them.

    Returns {"installed", "host_block", "app_up", "patches"}. REBOOT_LOADS is
    read (rpm -qp --scripts) only for the one row that is installable."""
    installed, ipatch = _read_installed(host)
    if installed["core"] is None:
        block = "netFLEX-CORE is not installed"
    elif ipatch:
        block = "an incremental patch (netFLEX-CORE-IPATCH) is installed \u2014 contact support"
    elif host.run(["test", "-f", incroot + "/.UPGRADE_LOCK"]).rc == 0:
        block = "an upgrade is in progress (/usr/cnc/.UPGRADE_LOCK)"
    elif (host.run(["test", "-e", stage_root]).rc == 0
          or host.run(["test", "-L", stage_root]).rc == 0):
        block = ("a staged upgrade is present (/usr/cnc_stage) \u2014 "
                 "finish or back out the upgrade first")
    else:
        block = None
    app_up = host.run(["test", "-f", incroot + "/.CNC_UP"]).rc == 0

    files = []
    for d in dirs:
        r = host.run(["find", d, "-maxdepth", "1", "-name", "*PATCH*.rpm"])
        if r.rc != 0:
            continue
        for path in sorted(r.out.split(), key=lambda x: x.rsplit("/", 1)[-1]):
            files.append(_read_file(host, path))

    rows = evaluate(installed, files, block, app_up)
    for i, row in enumerate(rows):
        if row["status"] == "installable":
            r = host.run(["rpm", "-qp", "--scripts", row["path"]])
            if r.rc == 0:
                files[i]["reboot_loads"] = parse_reboot_loads(r.out)
                rows = evaluate(installed, files, block, app_up)
            break
    return {"installed": installed, "host_block": block, "app_up": app_up,
            "patches": rows}
