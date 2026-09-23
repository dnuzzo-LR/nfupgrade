"""reports.py — before/after report snapshots (ported from nf-install's
ReportManager). A snapshot runs every report in a JSON list through Host.run and
stores one text file per report plus manifest.json. Timestamps and return codes
live only in the manifest so they never show up in a diff."""
import datetime
import json
import os
import re
import shutil
import time

REPORT_TIMEOUT = 300
KEEP = 20
STDERR_MARK = "----- STDERR -----"
_ID_RE = re.compile(r"^\d{8}_\d{6}_(before|after|manual)(_\d+)?$")


def load_reports(path):
    """Load report definitions as [{name, command}], skipping non-shell entries
    (nf-install's reports.json also lists its own menu functions)."""
    with open(path) as fh:
        data = json.load(fh)
    return [{"name": r["name"], "command": r["command"]}
            for r in data if r.get("type", "shell") == "shell"]


def safe_name(name):
    """A filename-safe form of a report name."""
    keep = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    return keep.replace(" ", "_") or "report"


def new_snapshot_id(root, label, when):
    """<YYYYmmdd_HHMMSS>_<label>, suffixed _2, _3... if that directory exists."""
    base = "{}_{}".format(when.strftime("%Y%m%d_%H%M%S"), label)
    sid, n = base, 2
    while os.path.exists(os.path.join(root, sid)):
        sid = "{}_{}".format(base, n)
        n += 1
    return sid


def _iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S")


def _write_manifest(snap_dir, man):
    # Write-then-rename so a reader never sees a half-written manifest.
    tmp = os.path.join(snap_dir, "manifest.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
    os.rename(tmp, os.path.join(snap_dir, "manifest.json"))


def take_snapshot(host, report_list, root, snap_id, label, mode=None, pair_of=None,
                  note="", on_progress=None, now=datetime.datetime.now, clock=time.time):
    """Run every report sequentially and store the results under root/snap_id.
    A failing, timed-out or unrunnable report is recorded, never raised.
    Returns the final manifest."""
    snap_dir = os.path.join(root, snap_id)
    os.makedirs(snap_dir)
    man = {"id": snap_id, "label": label, "mode": mode, "pair_of": pair_of,
           "started": _iso(now()), "finished": None, "status": "running",
           "note": note, "reports": []}
    _write_manifest(snap_dir, man)
    total = len(report_list)
    for idx, rep in enumerate(report_list, 1):
        if on_progress is not None:
            on_progress(idx, total, rep["name"])
        fname = "{:02d}_{}.txt".format(idx, safe_name(rep["name"]))
        entry = {"name": rep["name"], "command": rep["command"], "file": fname,
                 "rc": None, "secs": 0.0, "error": None}
        t0 = clock()
        try:
            r = host.run(["sh", "-c", rep["command"]], timeout=REPORT_TIMEOUT)
            text = r.out
            if r.err:
                if text and not text.endswith("\n"):
                    text += "\n"
                text += STDERR_MARK + "\n" + r.err
            if r.rc == 124 and r.err.startswith("timeout:"):
                entry["error"] = "timeout"
            else:
                entry["rc"] = r.rc
        except Exception as e:                  # a report must never abort the snapshot
            text = ""
            entry["error"] = str(e)
        entry["secs"] = round(clock() - t0, 2)
        with open(os.path.join(snap_dir, fname), "w") as fh:
            fh.write(text)
        man["reports"].append(entry)
        _write_manifest(snap_dir, man)
    man["status"] = "complete"
    man["finished"] = _iso(now())
    _write_manifest(snap_dir, man)
    return man


def read_manifest(root, snap_id):
    """The manifest of an existing snapshot, or None for an invalid/unknown id."""
    if not _ID_RE.match(snap_id or ""):
        return None
    try:
        with open(os.path.join(root, snap_id, "manifest.json")) as fh:
            return json.load(fh)
    except (IOError, OSError, ValueError):
        return None


def list_snapshots(root, live_id=None):
    """Manifest summaries (no per-report command), newest first. A snapshot still
    marked running that is not the live job was interrupted: report incomplete."""
    if not os.path.isdir(root):
        return []
    out = []
    for sid in sorted(os.listdir(root), reverse=True):
        man = read_manifest(root, sid)
        if man is None:
            continue
        if man["status"] == "running" and sid != live_id:
            man["status"] = "incomplete"
        man["reports"] = [dict((k, v) for k, v in r.items() if k != "command")
                          for r in man["reports"]]
        out.append(man)
    return out


def prune(root, keep=KEEP, protect=()):
    """Delete the oldest snapshots so at most `keep` remain, skipping `protect`."""
    ids = sorted(s for s in os.listdir(root) if _ID_RE.match(s))
    excess = len(ids) - keep
    for sid in ids:
        if excess <= 0:
            break
        if sid in protect:
            continue
        shutil.rmtree(os.path.join(root, sid), ignore_errors=True)
        excess -= 1


def report_text(root, snap_id, name):
    """The stored output of report `name` in a snapshot, or None if unknown."""
    man = read_manifest(root, snap_id)
    if man is None:
        return None
    for r in man["reports"]:
        if r["name"] == name:
            try:
                with open(os.path.join(root, snap_id, r["file"]), errors="replace") as fh:
                    return fh.read()
            except (IOError, OSError):
                return None
    return None
