from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    backend_port: int = 8011
    frontend_origin: str = "http://127.0.0.1:3011"
    database_url: str = "sqlite:///./data/db/niannian.db"
    asset_root: Path = Path("./data")
    max_audio_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    max_image_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    max_video_bytes: int = Field(default=500 * 1024 * 1024, ge=1024)

    asr_provider: str = "mock"
    asr_model_id: str = "paraformer-zh"
    ffmpeg_binary: str | None = None
    model_cache_root: Path = Path("./backend/.model-cache")

    llm_provider: str = "mock"
    llm_model: str = "qwen3.7-plus-2026-05-26"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str | None = None
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    tts_provider: str = "auto"
    tts_model: str = "cosyvoice-v3-flash"
    tts_voice: str = "longyuan_v3"
    tts_rate: float = Field(default=0.92, ge=0.5, le=2.0)

    keepsake_max_stories: int = Field(default=10, ge=1, le=30)
    keepsake_max_source_seconds: int = Field(default=1800, ge=30, le=10_800)
    keepsake_width: int = Field(default=1280, ge=640, le=3840)
    keepsake_height: int = Field(default=720, ge=360, le=2160)
    keepsake_ffmpeg_timeout_seconds: int = Field(default=900, ge=30, le=3600)

    @property
    def resolved_asset_root(self) -> Path:
        root = self.asset_root
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        return root.resolve()

    @property
    def resolved_database_url(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return self.database_url
        raw_path = self.database_url[len(prefix) :]
        if raw_path == ":memory:" or raw_path.startswith("/"):
            return self.database_url
        resolved = (PROJECT_ROOT / raw_path).resolve()
        return f"sqlite:///{resolved}"

    @property
    def resolved_model_cache_root(self) -> Path:
        root = self.model_cache_root
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        return root.resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
