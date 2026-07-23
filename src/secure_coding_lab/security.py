import hashlib
import hmac
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

USERNAME_PATTERN = re.compile(r"[a-z0-9_]{3,32}")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
CSRF_MAX_AGE_SECONDS = 60 * 60

password_hasher = PasswordHasher()
DUMMY_PASSWORD_HASH = password_hasher.hash("dummy-password-never-used")


def normalize_username(username: str) -> str:
    return username.strip().lower()


def is_valid_username(username: str) -> bool:
    return USERNAME_PATTERN.fullmatch(username) is not None


def is_valid_password(password: str) -> bool:
    return MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


def make_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def make_csrf_token(secret_key: str) -> str:
    nonce = secrets.token_urlsafe(24)
    timestamp = str(int(time.time()))
    payload = f"{nonce}.{timestamp}"
    signature = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def is_valid_csrf_token(
    form_token: str | None,
    cookie_token: str | None,
    secret_key: str,
    *,
    now: int | None = None,
) -> bool:
    if not form_token or not cookie_token or not hmac.compare_digest(form_token, cookie_token):
        return False

    try:
        nonce, raw_timestamp, signature = form_token.split(".", maxsplit=2)
        timestamp = int(raw_timestamp)
    except (TypeError, ValueError):
        return False

    if not nonce:
        return False

    current_time = int(time.time()) if now is None else now
    if timestamp > current_time or current_time - timestamp > CSRF_MAX_AGE_SECONDS:
        return False

    payload = f"{nonce}.{raw_timestamp}"
    expected = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
