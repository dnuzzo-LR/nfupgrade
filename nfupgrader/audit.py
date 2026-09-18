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
