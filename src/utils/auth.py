"""
Basic authentication for the LJS web interface.

Uses JWT tokens for session management. Passwords are hashed with bcrypt
directly (not passlib) to avoid the passlib/bcrypt version incompatibility.
JWT tokens are implemented inline with hmac + hashlib — no external JWT library.
"""

import hashlib
import hmac
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt as _bcrypt
from loguru import logger

from src.core.models import AuthConfig

SECRET_KEY_ENV = "LJS_WEB_SECRET"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
_BCRYPT_MAX_BYTES = 72


def load_auth_config() -> AuthConfig:
    """Load auth configuration from the environment.

    Reads ``LJS_WEB_SECRET`` from the environment. If set, uses it
    as the signing key. Otherwise generates a per-run ephemeral secret
    with a warning — safe for development and setup wizard flows.
    Set ``LJS_ALLOW_INSECURE_DEV=0`` (or just set ``LJS_WEB_SECRET``)
    to enforce production mode.

    Returns:
        An AuthConfig instance ready for injection.
    """
    secret = os.environ.get(SECRET_KEY_ENV)
    if secret:
        return AuthConfig(secret_key=secret)

    _allow_dev = os.environ.get("LJS_ALLOW_INSECURE_DEV", "").lower() in ("1", "true", "yes")
    if not _allow_dev:
        raise ValueError(
            f"{SECRET_KEY_ENV} is not set and LJS_ALLOW_INSECURE_DEV is not enabled. "
            f"Set {SECRET_KEY_ENV} in the environment or set LJS_ALLOW_INSECURE_DEV=1 for development."
        )
    import secrets
    dev_secret = secrets.token_urlsafe(32)
    logger.warning(
        f"No {SECRET_KEY_ENV} set — using ephemeral dev secret. "
        f"Set {SECRET_KEY_ENV} for a persistent key."
    )
    return AuthConfig(secret_key=dev_secret, allow_insecure_dev_secret=True)


def _normalize_password(password: str) -> bytes:
    """Pre-hash passwords exceeding bcrypt's 72-byte limit.

    bcrypt only uses the first 72 bytes of input. Longer passwords are
    SHA-256 pre-hashed to always produce a 64-char hex digest (well within
    the 72-byte limit). Shorter passwords pass through as-is.

    Args:
        password: The plaintext password.

    Returns:
        Bytes safe for bcrypt hashing (<= 72 bytes).
    """
    encoded = password.encode("utf-8")
    if len(encoded) > _BCRYPT_MAX_BYTES:
        return hashlib.sha256(encoded).hexdigest().encode("utf-8")
    return encoded


# ─── Inline HS256 JWT — no external dependency ──────────────────

def _b64url_encode(data: bytes) -> str:
    """Base64url-encode bytes with no padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Base64url-decode a string, adding padding."""
    padding = 4 - len(s) % 4
    s += "=" * padding
    return base64.urlsafe_b64decode(s)


JWT_HEADER = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())


class JWTError(Exception):
    """Raised when a JWT token is invalid or expired."""


def jwt_encode(payload: dict, secret: str) -> str:
    """Create an HS256 JWT token from a payload dict.

    Args:
        payload: Claims dict (e.g. {"sub": "admin", "exp": 1234567890}).
        secret: HMAC-SHA256 signing key.

    Returns:
        Encoded JWT string (header.payload.signature).
    """
    payload_b64 = _b64url_encode(json.dumps(payload).encode())
    message = f"{JWT_HEADER}.{payload_b64}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return f"{message}.{_b64url_encode(signature)}"


def jwt_decode(token: str, secret: str) -> dict:
    """Verify and decode an HS256 JWT token.

    Args:
        token: JWT string (header.payload.signature).
        secret: HMAC-SHA256 signing key.

    Returns:
        Decoded payload dict.

    Raises:
        JWTError: If the signature is invalid or the token is malformed.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("Invalid token format")
    header_b64, payload_b64, sig_b64 = parts
    if header_b64 != JWT_HEADER:
        raise JWTError("Invalid token header")
    message = f"{header_b64}.{payload_b64}"
    expected_sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    actual_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise JWTError("Invalid signature")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise JWTError(f"Invalid payload: {e}")
    return payload


# ─── AuthService ────────────────────────────────────────────────


class AuthService:
    """Handles password hashing and JWT token management."""

    def __init__(self, config: Optional[AuthConfig] = None, secret_key: Optional[str] = None):
        """Initialize AuthService.

        Args:
            config: AuthConfig with secret key and settings. Preferred.
            secret_key: Legacy direct secret key (used only if config is None).

        Raises:
            ValueError: If neither config nor secret_key provides a valid secret.
        """
        if config is not None:
            self._config = config
            self._secret = config.secret_key
        elif secret_key is not None:
            self._config = AuthConfig(secret_key=secret_key)
            self._secret = secret_key
        else:
            raise ValueError(
                "AuthService requires either a config or a secret_key. "
                f"Use {SECRET_KEY_ENV} env var or call load_auth_config()."
            )

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a plaintext password using bcrypt.

        Passwords longer than 72 bytes are SHA-256 pre-hashed to avoid
        bcrypt's input length limitation. Returns the hash as a string.
        """
        normalized = _normalize_password(password)
        return _bcrypt.hashpw(normalized, _bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        """Verify a plaintext password against a bcrypt hash."""
        try:
            normalized = _normalize_password(plain)
            return _bcrypt.checkpw(normalized, hashed.encode("utf-8"))
        except Exception:
            logger.debug("Password verification failed")
            return False

    def create_token(self, username: str) -> str:
        """Create a JWT access token for the given username."""
        expiry = self._config.token_expiry_minutes
        expire = datetime.now(timezone.utc) + timedelta(minutes=expiry)
        payload = {"sub": username, "exp": expire.timestamp()}
        return jwt_encode(payload, self._secret)

    def verify_token(self, token: str) -> Optional[str]:
        """Verify a JWT token and return the username, or None if invalid."""
        try:
            payload = jwt_decode(token, self._secret)
            if payload.get("exp", 0) < datetime.now(timezone.utc).timestamp():
                return None
            return payload.get("sub")
        except JWTError:
            return None