"""patches.py — pure eligibility rules for netFLEX-CORE-PATCH RPMs.

No I/O: callers gather installed state and per-file rpm metadata and pass
them to evaluate(), which decides which single patch may be installed next
(strict sequence) and labels every other file with why it cannot be."""
import json
import re

from nfupgrader import cfg, localinfo

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



def _highest(versions, generic=None):
    """Highest of several version strings: by (int load, string). With
    `generic`, only versions of that generic count (None if there are none):
    a leftover PATCH of another release must not hide the current one."""
    if generic is not None:
        versions = [v for v in versions
                    if (split_version(v) or (None,))[0] == generic]

    def key(v):
        sp = split_version(v)
        return (sp[1] if sp else -1, v)
    return max(versions, key=key) if versions else None


#: netFLEX's web GUI version tool: prints the release (generic) and build
#: (load) compiled into the web GUI (gui/src/web/webinfo.c).
WEBINFO = "/web/bin/webinfo"


def parse_webinfo(out):
    """(release, int build) from `webinfo -j` output, or None if unreadable.
    The build is compared as a number ("036" == 36)."""
    try:
        d = json.loads(out or "")
        build = str(d["build"]).strip()
        release = str(d["release"]).strip()
    except (ValueError, KeyError, TypeError):
        return None
    if not release or not build.isdigit():
        return None
    return release, int(build)


def web_status(host, installed, incroot="/usr/cnc"):
    """The web GUI's version against the installed CORE/PATCH load:
    {"web": "5.4.0.35", "expected": "5.4.0.36", "matches": False}. A patch
    RPM does not carry the web GUI (it is a separate LINUXWEB64 tar), so the
    two drift apart unless the tar is installed too. None when webinfo cannot
    be run or read; expected/matches None when netFLEX-CORE is not installed."""
    r = host.run([incroot + WEBINFO, "-j"])
    web = parse_webinfo(r.out) if r.rc == 0 else None
    if web is None:
        return None
    core = split_version(installed.get("core"))
    load = current_load(installed)
    if core is None or load is None:
        return {"web": "{}.{}".format(*web), "expected": None, "matches": None}
    return {"web": "{}.{}".format(*web), "expected": "{}.{}".format(core[0], load),
            "matches": web == (core[0], load)}


_VERSION_ID_RE = re.compile(r'^VERSION_ID="?(\d+)', re.M)

#: nf_webbuild results, keyed (path, size, mtime): the Patches tab polls every
#: few seconds and unpacking a web tar's lwebdata.Z is not cheap.
_TAR_BUILDS = {}


def rhel_major(host):
    """The RHEL major ("8") from /etc/os-release VERSION_ID, or None."""
    r = host.run(["cat", "/etc/os-release"])
    m = _VERSION_ID_RE.search(r.out) if r.rc == 0 else None
    return m.group(1) if m else None


def web_tar_candidates(host, rpm_dir, major):
    """LINUXWEB*.tar paths at the top of rpm_dir that are for this RHEL major
    (name has ".rhel<major>."), excluding ATT tars, sorted by name. Nothing
    inside a tar records its RHEL major, so the name is the only source."""
    if not major:
        return []
    r = host.run(["find", rpm_dir, "-maxdepth", "1", "-name", "LINUXWEB*.tar"])
    if r.rc != 0:
        return []
    tag = ".rhel{}.".format(major)
    names = [ln.strip() for ln in r.out.splitlines() if ln.strip()]
    keep = [n for n in names
            if "ATT" not in n.rsplit("/", 1)[-1] and tag in n.rsplit("/", 1)[-1]]
    return sorted(keep, key=lambda x: x.rsplit("/", 1)[-1])


def read_tar_build(host, path, webbuild):
    """(release, int build) of the web GUI inside the tar at path, via
    nf_webbuild (the tar's own webinfo -j); None if it cannot be read. The
    file name is never trusted for the build."""
    st = host.run(["stat", "-c", "%s %Y", path])
    if st.rc != 0:
        return None
    key = (path, st.out.strip())
    if key in _TAR_BUILDS:
        return _TAR_BUILDS[key]
    r = host.run([webbuild, path], timeout=120)
    build = parse_webinfo(r.out) if r.rc == 0 else None
    # Cache only nf_webbuild's own answers (0 = build, 1 = unreadable). A
    # timeout (124) or a missing/killed helper is not a fact about the tar:
    # the next scan tries again.
    if r.rc in (0, 1):
        _TAR_BUILDS[key] = build
    return build


_NUMBERED_RE = re.compile(r'^LINUXWEB64\.\d+\.rhel')


def pick_web_tar(version, tars):
    """Choose the tar to install with patch `version` from [(path, build)]
    (build = (release, int) or None). Returns (path, None) for a match --
    preferring a numbered name (LINUXWEB64.<n>.rhel...), else the first by
    name -- or (None, note) saying why none can be installed."""
    want = split_version(version)
    named = sorted(tars, key=lambda t: t[0].rsplit("/", 1)[-1])
    match = [t[0] for t in named if want is not None and t[1] == want]
    if match:
        numbered = [m for m in match if _NUMBERED_RE.match(m.rsplit("/", 1)[-1])]
        return (numbered or match)[0], None
    found = []
    for _, b in named:
        if b is not None and "{}.{}".format(*b) not in found:
            found.append("{}.{}".format(*b))
    if found:
        return None, "no web GUI tar for {} found (found: {})".format(version, ", ".join(found))
    if named:
        return None, "could not read the web GUI build from {}".format(
            ", ".join(t[0].rsplit("/", 1)[-1] for t in named))
    return None, "no web GUI tar for {} found".format(version)


def _web_tar_fields(host, row, webbuild):
    """web_tar / web_tar_note for the installable row: only tars beside its RPM."""
    rpm_dir = row["path"].rsplit("/", 1)[0] or "/"
    paths = web_tar_candidates(host, rpm_dir, rhel_major(host))
    tar, note = pick_web_tar(row["version"],
                             [(t, read_tar_build(host, t, webbuild)) for t in paths])
    row["web_tar"], row["web_tar_note"] = tar, note


def installed_versions(host):
    """The installed CORE/PATCH dict (see _read_installed), without IPATCH."""
    return _read_installed(host)[0]


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
        core_sp = split_version(core)
        if core_sp is not None:
            patch = _highest([ln.strip() for ln in r.out.splitlines() if ln.strip()],
                             core_sp[0])
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


def gather(host, dirs, stage_root="/usr/cnc_stage", incroot="/usr/cnc", webbuild=None):
    """Collect installed state and depot patch files, then evaluate them.

    Returns {"installed", "host_block", "app_up", "patches"}. REBOOT_LOADS is
    read (rpm -qp --scripts) only for the one row that is installable. With
    webbuild (path of nf_webbuild), that row also gets web_tar (the matching
    web GUI tar beside its RPM, or None) and web_tar_note (why none)."""
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
    app_up = localinfo.app_state(host, incroot) == "up"

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
    if webbuild:
        for row in rows:
            if row["status"] == "installable":
                _web_tar_fields(host, row, webbuild)
    return {"installed": installed, "host_block": block, "app_up": app_up,
            "patches": rows, "web": web_status(host, installed, incroot)}
