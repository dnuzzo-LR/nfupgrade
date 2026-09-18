"""auth.py — single-use token exchanged for an IP-bound session cookie.

The token is the whole credential (decision 10); loopback binding (decision 11)
keeps it off the network. Redemption is one-shot; the session id is then required
and is tied to the client IP."""
import secrets


class TokenAuth(object):
    def __init__(self, token=None):
        self.token = token or secrets.token_urlsafe(24)
        self._redeemed = False
        self._sessions = {}   # sid -> client_ip

    def redeem(self, token, client_ip):
        """Exchange the (single-use) token for a new session id, or None."""
        if self._redeemed or token != self.token:
            return None
        self._redeemed = True
        sid = secrets.token_urlsafe(24)
        self._sessions[sid] = client_ip
        return sid

    def valid_session(self, sid, client_ip):
        """True iff sid is known and bound to this client_ip."""
        return self._sessions.get(sid) == client_ip
