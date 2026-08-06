from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "backend"


class Settings(BaseSettings):
    app_name: str = "Chalk Web API"
    environment: str = Field(default="development", alias="CHALK_WEB_ENV")
    session_secret: str = Field(default="chalk-web-local-dev-secret", alias="CHALK_SESSION_SECRET")
    session_cookie_name: str = Field(default="chalk_session", alias="CHALK_SESSION_COOKIE")
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, alias="CHALK_SESSION_TTL_SECONDS")
    cors_origins: str = Field(default="http://localhost:3000,http://127.0.0.1:3000", alias="CHALK_CORS_ORIGINS")
    secure_cookies: bool = Field(default=False, alias="CHALK_SECURE_COOKIES")
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, alias="CHALK_MAX_UPLOAD_BYTES")
    max_analysis_chars: int = Field(default=200_000, alias="CHALK_MAX_ANALYSIS_CHARS")
    qwen_input_cost_per_million_cny: str | None = Field(
        default="12",
        alias="QWEN_INPUT_COST_PER_MILLION_CNY",
    )
    qwen_output_cost_per_million_cny: str | None = Field(
        default="36",
        alias="QWEN_OUTPUT_COST_PER_MILLION_CNY",
    )
    data_dir: Path = Field(default=PROJECT_ROOT / "data", alias="CHALK_WEB_DATA_DIR")
    legacy_root: Path = Field(default=PROJECT_ROOT, alias="CHALK_LEGACY_ROOT")

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    def validate_runtime(self) -> None:
        if not self.is_production:
            return
        if (
            len(self.session_secret) < 32
            or self.session_secret == "chalk-web-local-dev-secret"
            or self.session_secret.startswith("replace-with-")
        ):
            raise ValueError("Production requires a non-default session secret of at least 32 characters.")
        if not self.secure_cookies:
            raise ValueError("Production requires CHALK_SECURE_COOKIES=true.")
        for origin in self.cors_origin_list:
            if origin == "*" or urlsplit(origin).scheme != "https":
                raise ValueError("Production CORS origins must be explicit HTTPS origins.")

    @property
    def session_store_path(self) -> Path:
        return self.data_dir / "sessions.json"

    @property
    def api_key_store_path(self) -> Path:
        return self.data_dir / "api_keys.json"

    @property
    def web_db_path(self) -> Path:
        return self.data_dir / "web.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def analysis_assets_dir(self) -> Path:
        return self.data_dir / "analysis-assets"

    @property
    def multimodal_assets_dir(self) -> Path:
        return self.data_dir / "multimodal-assets"

    @property
    def modeling_assets_dir(self) -> Path:
        return self.data_dir / "modeling-assets"

    @property
    def science125_context_index_path(self) -> Path:
        return self.data_dir / "science125" / "science125-context-v1.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    if not settings.data_dir.is_absolute():
        settings.data_dir = (PROJECT_ROOT / settings.data_dir).resolve()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CHALK_PROJECT_ROOT", str(PROJECT_ROOT))
    os.environ["CHALK_WEB_DATA_DIR"] = str(settings.data_dir.resolve())
    os.environ.setdefault("CHALK_WEB_ENV", settings.environment)
    return settings
