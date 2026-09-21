"""checks.py — pre-flight/post-flight check engine. Each kind is a small
evaluator fn(host, params) -> (passed: bool, detail: str) run through Host.run.
A JSON list binds kinds to a moment (preflight/postflight), a phase, and an
optional MACHTYPE. Pre-flight ERRORs block Launch (override logged); everything
post-flight is advisory."""
import json
import re

from nfupgrader import localinfo


def _rpm_installed(host, p):
    r = host.run(["rpm", "-q", p["pkg"]])
    return r.rc == 0, r.out.strip() or r.err.strip()


def _file_exists(host, p):
    return host.run(["test", "-f", p["path"]]).rc == 0, p["path"]


def _file_absent(host, p):
    return host.run(["test", "-f", p["path"]]).rc != 0, p["path"]


def _symlink_target(host, p):
    r = host.run(["readlink", p["path"]])
    tgt = r.out.strip()
    if r.rc != 0 or not tgt:
        return False, "no symlink at {}".format(p["path"])
    if "contains" in p and p["contains"] not in tgt:
        return False, "{} -> {}".format(p["path"], tgt)
    return True, "{} -> {}".format(p["path"], tgt)


def _command_status(host, p):
    r = host.run(list(p["argv"]))
    return r.rc == p.get("expect_rc", 0), "rc={}".format(r.rc)


def _command_output_match(host, p):
    r = host.run(list(p["argv"]))
    found = re.search(p["pattern"], r.out + r.err) is not None
    if p.get("negate"):
        return (not found), ("matched" if found else "absent")
    return found, ("matched" if found else "not found")


def _disk_space_min(host, p):
    rows = localinfo.parse_df(host.run(["df", "-kP"]).out, keep=(p["mount"],))
    if not rows or rows[0]["avail_kb"] is None:
        return False, "no df row for {}".format(p["mount"])
    free_gb = rows[0]["avail_kb"] / (1024.0 * 1024.0)
    return free_gb >= p["min_gb"], "{:.1f} GB free".format(free_gb)


def _service_active(host, p):
    r = host.run(["systemctl", "is-active", p["name"]])
    return r.rc == 0, r.out.strip()


def _dbcheck_clean(host, p):
    n = localinfo.parse_dbcheck_errors(host.run(["dbcheck", "-AV"]).out)
    return n == 0, "{} error(s)".format(n)


def _staged_present(host, p):
    r = host.run(["readlink", "/usr/cnc_stage"])
    return (r.rc == 0 and bool(r.out.strip())), r.out.strip() or "no staged load"


KINDS = {
    "rpm_installed": _rpm_installed,
    "file_exists": _file_exists,
    "file_absent": _file_absent,
    "symlink_target": _symlink_target,
    "command_status": _command_status,
    "command_output_match": _command_output_match,
    "disk_space_min": _disk_space_min,
    "service_active": _service_active,
    "dbcheck_clean": _dbcheck_clean,
    "staged_present": _staged_present,
}


def load_checks(path):
    """Load a JSON list of check definitions."""
    with open(path) as fh:
        return json.load(fh)


def _applies(check, when, phase, machtype):
    if check.get("when") != when:
        return False
    cphase = check.get("phase", "any")
    if cphase not in ("any", phase):
        return False
    cmt = check.get("machtype")
    if cmt is not None and cmt != machtype:
        return False
    return True


def run_checks(host, check_list, when, phase, machtype=None):
    """Evaluate every check bound to (when, phase, machtype). Returns a list of
    result dicts: {name, description, severity, passed, detail, blocking}."""
    results = []
    for c in check_list:
        if not _applies(c, when, phase, machtype):
            continue
        kind = KINDS.get(c["kind"])
        if kind is None:
            passed, detail = False, "unknown kind: {}".format(c["kind"])
        else:
            try:
                passed, detail = kind(host, c.get("params", {}))
            except Exception as e:                       # a check must never crash the run
                passed, detail = False, "check error: {}".format(e)
        severity = c.get("fail_text", "ERROR")
        blocking = (when == "preflight" and severity == "ERROR" and not passed)
        results.append({
            "name": c.get("name", c["kind"]),
            "description": c.get("description", ""),
            "severity": severity,
            "passed": passed,
            "detail": detail,
            "blocking": blocking,
        })
    return results


def blocking_failures(results):
    """The subset of results that block a launch."""
    return [r for r in results if r["blocking"]]
