"""Single-owner admin login.

Production: the password comes from the ADMIN_PASSWORD env var (the host's disk is wiped on restart).
Local: first visit to /admin/ sets a password, stored as a scrypt hash in data/auth.json.
Sessions are signed tokens, so they survive restarts as long as SESSION_SECRET stays the same.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from .store import DATA_DIR

AUTH_PATH = os.path.join(DATA_DIR, "auth.json")
SESSION_TTL = 12 * 60 * 60
MIN_PASSWORD = 10
MAX_FAILS, FAIL_WINDOW = 5, 10 * 60

_SECRET = (os.environ.get("SESSION_SECRET") or secrets.token_hex(32)).encode()
_fails = {}
_lock = threading.Lock()


def _hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _env_password():
    return os.environ.get("ADMIN_PASSWORD") or None


def is_configured():
    return bool(_env_password()) or os.path.exists(AUTH_PATH)


def setup(password):
    if is_configured():
        raise PermissionError("ตั้งรหัสผ่านไปแล้ว")
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise ValueError(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
    salt = secrets.token_bytes(16)
    record = {
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(_hash(password, salt)).decode(),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    fd = os.open(AUTH_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)


def locked_out(ip):
    now = time.time()
    with _lock:
        recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        _fails[ip] = recent
        return len(recent) >= MAX_FAILS


def verify(password, ip):
    if not is_configured() or not isinstance(password, str):
        return False
    env = _env_password()
    if env:
        ok = hmac.compare_digest(hashlib.sha256(password.encode()).digest(), hashlib.sha256(env.encode()).digest())
    else:
        with open(AUTH_PATH) as f:
            record = json.load(f)
        ok = hmac.compare_digest(base64.b64decode(record["hash"]), _hash(password, base64.b64decode(record["salt"])))
    if not ok:
        with _lock:
            _fails.setdefault(ip, []).append(time.time())
    return ok


def _sign(payload):
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def new_session():
    payload = f"{int(time.time() + SESSION_TTL)}.{secrets.token_urlsafe(8)}"
    return f"{payload}.{_sign(payload)}"


def check(token):
    if not token or token.count(".") != 2:
        return False
    expires, nonce, signature = token.split(".")
    if not hmac.compare_digest(signature, _sign(f"{expires}.{nonce}")):
        return False
    return expires.isdigit() and int(expires) > time.time()


def revoke(token):
    """Tokens are stateless; logging out clears the cookie in the browser."""
