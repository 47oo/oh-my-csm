"""Application settings via environment variables."""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Oh-My-CSM"
    app_env: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = "postgresql+asyncpg://csm:csm_dev_password@localhost:5432/oh_my_csm"

    jwt_secret_key: str = "CHANGE_ME_DEV_ONLY_JWT_SECRET_KEY_32BYTES_MIN"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # Versioned AES-256-GCM keys for credentials (base64 encoded 32-byte keys), injected via env
    csm_secret_keys: str = (
        '{"1": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}'
    )
    csm_active_key_version: str = "1"

    # Security policy
    max_failed_login_attempts: int = 5
    lockout_minutes: int = 15

    # Bootstrap admin defaults (used by CLI seed)
    initial_admin_username: str = "admin"
    initial_admin_password: str = "Admin123456"
    initial_admin_display_name: str = "系统管理员"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        stripped = self.cors_origins.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in stripped.split(",") if item.strip()]

    def parsed_secret_keys(self) -> dict[str, str]:
        data = json.loads(self.csm_secret_keys)
        if not isinstance(data, dict) or not data:
            raise ValueError("CSM_SECRET_KEYS must be a non-empty JSON object")
        return {str(k): str(v) for k, v in data.items()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
