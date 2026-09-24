"""audit.py — append-only JSONL audit log. Every service action is recorded
here; the requirement Dan set as the condition for the auth/transport risks."""
import datetime
import json


class AuditLog(object):
    def __init__(self, path):
        self.path = path

    def record(self, event, **fields):
        """Append one JSON object: {ts, event, **fields}. ts is ISO-8601 UTC."""
        entry = {"ts": datetime.datetime.utcnow().isoformat() + "Z", "event": event}
        entry.update(fields)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry


def last_unfinished_fix(path):
    """The most recent check_fix_start in the audit log at `path` that has no
    later check_fix for the same check name -- i.e. a fix interrupted by e.g.
    a service restart mid-fix. Returns {"name", "kind", "ts"} or None. Only
    one fix runs at a time (api._FIX_LOCK), so at most the very last
    check_fix_start in the log can be unfinished. Tolerates a missing file
    and malformed lines (read once, best-effort)."""
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except (IOError, OSError):
        return None
    last_start = None
    finished = False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        event = entry.get("event")
        if event == "check_fix_start":
            last_start = entry
            finished = False
        elif (event == "check_fix" and last_start is not None
              and entry.get("name") == last_start.get("name")):
            finished = True
    if last_start is None or finished:
        return None
    return {"name": last_start.get("name"), "kind": last_start.get("kind"),
            "ts": last_start.get("ts")}
