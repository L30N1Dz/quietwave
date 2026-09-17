"""Configuration, loaded from the environment / .env once at import time."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Themes that ship with the app. Keep in sync with static/themes/themes.json;
# tests/test_themes.py enforces that they agree.
KNOWN_THEMES = ("quietwave", "relay-nine", "emberlink", "neonwire")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Twilio ----
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""

    # ---- Public origin ----
    # Signature validation is built from this, never from the inbound request.
    public_base_url: str = "http://127.0.0.1:8090"

    # ---- Access ----
    app_passphrase: str = ""
    session_secret: str = ""

    # ---- Storage ----
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "quietwave"
    store_backend: Literal["mongo", "memory"] = Field(
        default="mongo", validation_alias="QUIETWAVE_STORE"
    )
    media_root: Path = Field(
        default=Path("./data/media"), validation_alias="QUIETWAVE_MEDIA_ROOT"
    )

    # ---- Interface ----
    theme: str = Field(default="quietwave", validation_alias="QUIETWAVE_THEME")
    host: str = Field(default="127.0.0.1", validation_alias="QUIETWAVE_HOST")
    port: int = Field(default=8090, validation_alias="QUIETWAVE_PORT")
    poll_interval_ms: int = Field(
        default=5000, validation_alias="QUIETWAVE_POLL_INTERVAL_MS"
    )

    # ---- Behaviour ----
    validate_twilio_signature: bool = Field(
        default=True, validation_alias="QUIETWAVE_VALIDATE_TWILIO_SIGNATURE"
    )
    default_country_code: str = Field(
        default="1", validation_alias="QUIETWAVE_DEFAULT_COUNTRY_CODE"
    )
    outbound_media_ttl_hours: int = Field(
        default=24, validation_alias="QUIETWAVE_OUTBOUND_MEDIA_TTL_HOURS"
    )
    session_ttl_days: int = Field(
        default=180, validation_alias="QUIETWAVE_SESSION_TTL_DAYS"
    )
    cookie_secure: bool = Field(
        default=True, validation_alias="QUIETWAVE_COOKIE_SECURE"
    )
    max_upload_bytes: int = Field(
        default=5 * 1024 * 1024, validation_alias="QUIETWAVE_MAX_UPLOAD_BYTES"
    )
    # Seeds fake traffic into the in-memory store so the interface (and all
    # four themes) can be explored without Twilio or MongoDB. Never seeds a
    # real database -- it is ignored unless the store backend is "memory".
    seed_demo: bool = Field(default=False, validation_alias="QUIETWAVE_DEMO")

    @field_validator("public_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("theme")
    @classmethod
    def _known_theme(cls, v: str) -> str:
        if v not in KNOWN_THEMES:
            raise ValueError(
                f"unknown theme {v!r}; expected one of {', '.join(KNOWN_THEMES)}"
            )
        return v

    # ------------------------------------------------------------------
    @property
    def webhook_sms_url(self) -> str:
        """The exact URL Twilio signs against for inbound messages."""
        return f"{self.public_base_url}/webhooks/twilio/sms"

    @property
    def webhook_status_url(self) -> str:
        """The exact URL Twilio signs against for delivery receipts."""
        return f"{self.public_base_url}/webhooks/twilio/status"

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    def startup_problems(self) -> list[str]:
        """Misconfiguration that should stop the app from booting.

        Deliberately loud: a station that starts but cannot send, or that
        accepts any passphrase, is worse than one that refuses to start.
        """
        problems: list[str] = []
        placeholder = ("", "change-me", "change-me-to-something-long",
                       "change-me-generate-a-real-one")

        if self.store_backend == "memory":
            # Demo/test mode: nothing else is required, everything is ephemeral.
            return problems

        if not self.twilio_configured:
            problems.append(
                "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set "
                "(or run with QUIETWAVE_STORE=memory for a themes-only demo)."
            )
        if self.app_passphrase in placeholder:
            problems.append("APP_PASSPHRASE must be set to a real passphrase.")
        elif len(self.app_passphrase) < 8:
            problems.append("APP_PASSPHRASE is too short; use at least 8 characters.")
        if self.session_secret in placeholder:
            problems.append(
                "SESSION_SECRET must be set. Generate one with: "
                'python -c "import secrets;print(secrets.token_urlsafe(48))"'
            )
        if self.validate_twilio_signature and not self.public_base_url.startswith(
            "https://"
        ):
            problems.append(
                f"PUBLIC_BASE_URL is {self.public_base_url!r} but signature "
                "validation is on; Twilio signs the https URL it actually calls."
            )
        return problems

    def effective_session_secret(self) -> str:
        """Never sign cookies with a blank key; memory mode gets a random one."""
        return self.session_secret or secrets.token_urlsafe(48)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
