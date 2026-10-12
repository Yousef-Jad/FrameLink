import hashlib
import hmac
import secrets
import threading
import time


class PairingTokens:
    def __init__(self, ttl=600, now=time.monotonic):
        self.ttl, self.now = ttl, now
        self.digest = None
        self.current_token = None
        self.session_id = None
        self.expires = 0.0
        self.consumed = False
        self.lock = threading.Lock()

    def get_or_create(self):
        with self.lock:
            now_t = self.now()
            if self.current_token and not self.consumed and now_t < self.expires:
                return self.current_token
            return self._issue_locked()

    def issue(self):
        with self.lock:
            return self._issue_locked()

    def _issue_locked(self):
        token = secrets.token_urlsafe(32)
        self.current_token = token
        self.session_id = secrets.token_hex(4)
        self.digest = hashlib.sha256(token.encode()).digest()
        self.expires = self.now() + self.ttl
        self.consumed = False
        return token

    def is_active(self):
        with self.lock:
            return bool(self.digest and not self.consumed and self.now() < self.expires)

    def is_expired(self):
        with self.lock:
            return bool(self.current_token and (self.now() >= self.expires) and not self.consumed)

    def remaining_seconds(self):
        with self.lock:
            if not self.current_token or self.consumed:
                return 0
            return max(0, int(self.expires - self.now()))

    def consume(self, token):
        if not isinstance(token, str) or len(token) > 128:
            return False
        with self.lock:
            if self.digest is None or self.now() >= self.expires or self.consumed:
                return False
            valid = hmac.compare_digest(self.digest, hashlib.sha256(token.encode()).digest())
            if valid:
                self.consumed = True
                self.digest = None
                self.current_token = None
            return valid

    def revoke(self):
        with self.lock:
            self.digest = None
            self.current_token = None
            self.consumed = True

    def invalidate(self):
        with self.lock:
            self.expires = self.now() - 1.0


class RateLimiter:
    def __init__(self, count=12, window=60):
        self.count, self.window = count, window
        self.attempts = {}

    def allow(self, address):
        now = time.monotonic()
        self.attempts = {
            k: [t for t in v if t > now - self.window]
            for k, v in self.attempts.items()
            if v and v[-1] > now - self.window
        }
        if len(self.attempts) > 1024 and address not in self.attempts:
            return False
        times = self.attempts.setdefault(address, [])
        if len(times) >= self.count:
            return False
        times.append(now)
        return True
