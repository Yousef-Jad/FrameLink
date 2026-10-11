import hashlib
import hmac
import secrets
import threading
import time


class PairingTokens:
    def __init__(self, ttl=120, now=time.monotonic):
        self.ttl, self.now = ttl, now
        self.digest = None
        self.expires = 0.0
        self.lock = threading.Lock()

    def issue(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.digest = hashlib.sha256(token.encode()).digest()
            self.expires = self.now() + self.ttl
        return token

    def consume(self, token):
        if not isinstance(token, str) or len(token) > 128:
            return False
        with self.lock:
            if self.digest is None or self.now() >= self.expires:
                return False
            valid = hmac.compare_digest(self.digest, hashlib.sha256(token.encode()).digest())
            if valid:
                self.digest = None
            return valid

    def revoke(self):
        with self.lock:
            self.digest = None


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
