"""Password hashing, JWT, PAT tokens, and AES-256-GCM credential encryption."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_password_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=2,
)

PAT_PREFIX = "csm_"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_password_strength(password: str) -> None:
    """Enforce baseline ops password strength: >= 8 chars with letters and digits."""
    if len(password) < 8:
        raise ValueError("密码长度至少为 8 位")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("密码必须同时包含字母和数字")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(
    subject: int,
    username: str,
    expires_delta: timedelta | None = None,
) -> tuple[str, str, datetime]:
    settings = get_settings()
    now = utc_now()
    expire = now + (expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes))
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": str(subject),
        "username": username,
        "type": "access",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expire


def create_refresh_token(
    subject: int,
    username: str,
    expires_delta: timedelta | None = None,
) -> tuple[str, str, datetime]:
    settings = get_settings()
    now = utc_now()
    expire = now + (expires_delta or timedelta(days=settings.jwt_refresh_token_expire_days))
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": str(subject),
        "username": username,
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expire


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )


def generate_api_token() -> tuple[str, str, str]:
    """Generate a Personal Access Token: returns (raw_token, display_prefix, sha256_hash)."""
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(32)}"
    prefix = raw[:12]
    digest = hash_api_token(raw)
    return raw, prefix, digest


def hash_api_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class CredentialCipher:
    """Versioned AES-256-GCM encryption for optional sensitive credentials."""

    def __init__(self, keys: dict[str, str], active_version: str) -> None:
        self._keys: dict[str, bytes] = {}
        for ver, b64_key in keys.items():
            raw = base64.b64decode(b64_key)
            if len(raw) != 32:
                raise ValueError(f"Secret key version '{ver}' must decode to 32 bytes (AES-256)")
            self._keys[ver] = raw
        if active_version not in self._keys:
            raise ValueError(f"Active key version '{active_version}' not found in CSM_SECRET_KEYS")
        self.active_version = active_version

    @classmethod
    def from_settings(cls) -> CredentialCipher:
        settings = get_settings()
        return cls(settings.parsed_secret_keys(), settings.csm_active_key_version)

    def encrypt(self, plaintext: str, version: str | None = None) -> tuple[str, str]:
        ver = version or self.active_version
        key = self._keys[ver]
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        encoded = (
            f"v{ver}:"
            f"{base64.b64encode(nonce).decode('ascii')}:"
            f"{base64.b64encode(ciphertext).decode('ascii')}"
        )
        return encoded, ver

    def decrypt(self, payload: str) -> str:
        parts = payload.split(":")
        if len(parts) != 3 or not parts[0].startswith("v"):
            raise ValueError("Invalid encrypted credential format")
        ver = parts[0][1:]
        if ver not in self._keys:
            raise ValueError(f"Missing decryption key for version '{ver}'")
        nonce = base64.b64decode(parts[1])
        ciphertext = base64.b64decode(parts[2])
        aesgcm = AESGCM(self._keys[ver])
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")
