"""reports.py — before/after report snapshots (ported from nf-install's
ReportManager). A snapshot runs every report in a JSON list through Host.run and
stores one text file per report plus manifest.json. Timestamps and return codes
live only in the manifest so they never show up in a diff."""
import datetime
import difflib
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


MAX_ROWS = 2000
CONTEXT = 3


def _failed(entry):
    return entry["error"] is not None or entry["rc"] != 0


def _lines(root, snap_id, name):
    text = report_text(root, snap_id, name)
    return (text or "").splitlines()


def _counts(a_lines, b_lines):
    """(added, removed) line counts between two texts."""
    if a_lines == b_lines:
        return 0, 0
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a_lines, b_lines).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def compare(root, a, b):
    """Per-report status between snapshots a and b (A's order, then B-only
    reports), or None if either snapshot is unknown."""
    ma, mb = read_manifest(root, a), read_manifest(root, b)
    if ma is None or mb is None:
        return None
    ea = dict((r["name"], r) for r in ma["reports"])
    eb = dict((r["name"], r) for r in mb["reports"])
    names = [r["name"] for r in ma["reports"]] + \
            [r["name"] for r in mb["reports"] if r["name"] not in ea]
    rows = []
    for name in names:
        if name not in ea or name not in eb:
            rows.append({"name": name, "status": "missing", "added": 0, "removed": 0,
                         "side": "a" if name in ea else "b"})
            continue
        added, removed = _counts(_lines(root, a, name), _lines(root, b, name))
        if _failed(ea[name]) or _failed(eb[name]):
            status = "failed"
        elif added or removed:
            status = "changed"
        else:
            status = "unchanged"
        rows.append({"name": name, "status": status, "added": added, "removed": removed})
    return {"a": a, "b": b, "reports": rows}


def unified_rows(a_lines, b_lines, context=CONTEXT):
    """Unified-diff rows: {op: ' '|'+'|'-'|'@', text}."""
    rows = []
    for i, ln in enumerate(difflib.unified_diff(a_lines, b_lines, lineterm="", n=context)):
        if i < 2:                       # the ---/+++ file header lines
            continue
        if ln.startswith("@@"):
            rows.append({"op": "@", "text": ln})
        else:
            rows.append({"op": ln[:1] or " ", "text": ln[1:]})
    return rows


def side_rows(a_lines, b_lines, context=CONTEXT):
    """Side-by-side rows: {op: equal|replace|insert|delete, left, right}, with long
    equal runs collapsed to `context` lines each side around a {op: skip, count}."""
    rows = []
    ops = difflib.SequenceMatcher(None, a_lines, b_lines).get_opcodes()
    last = len(ops) - 1
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            n = i2 - i1
            head = context if k > 0 else 0
            tail = context if k < last else 0
            if n > head + tail:
                shown = list(range(head)) + [None] + list(range(n - tail, n))
            else:
                shown = list(range(n))
            for off in shown:
                if off is None:
                    rows.append({"op": "skip", "count": n - head - tail})
                else:
                    rows.append({"op": "equal", "left": a_lines[i1 + off],
                                 "right": b_lines[j1 + off]})
            continue
        for off in range(max(i2 - i1, j2 - j1)):
            left = a_lines[i1 + off] if i1 + off < i2 else None
            right = b_lines[j1 + off] if j1 + off < j2 else None
            rows.append({"op": tag, "left": left, "right": right})
    return rows


def diff(root, a, b, name, style="side", cap=MAX_ROWS):
    """Diff rows for one report between two snapshots (a side missing the report
    diffs as empty), or None if neither snapshot has it."""
    at, bt = report_text(root, a, name), report_text(root, b, name)
    if at is None and bt is None:
        return None
    al, bl = (at or "").splitlines(), (bt or "").splitlines()
    rows = unified_rows(al, bl) if style == "unified" else side_rows(al, bl)
    return {"rows": rows[:cap], "truncated": len(rows) > cap}
