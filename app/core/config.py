"""
app/core/config.py
──────────────────
Centralised settings loaded from environment variables / .env file.
"""

from typing import Optional
import pytz

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── Mistral ──────────────────────────────────────────
    mistral_api_key: str
    mistral_model: str = "mistral-small-2506"

    # ── Twilio ───────────────────────────────────────────
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    # ── App ──────────────────────────────────────────────
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False

    # ── Database ─────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./agent.db"

    # ── Agent behaviour ───────────────────────────────────
    summarize_every_n_messages: int = 20
    min_typing_delay: float = 1.0
    max_typing_delay: float = 3.5

    # ── Timezone ──────────────────────────────────────────
    # IANA timezone string, e.g. "Asia/Kolkata", "America/New_York"
    user_timezone: str = "Asia/Kolkata"

    # ── Tools ─────────────────────────────────────────────
    tavily_api_key: Optional[str] = None


settings = Settings()
