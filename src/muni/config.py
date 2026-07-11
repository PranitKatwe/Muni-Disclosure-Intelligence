from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MUNI_", extra="ignore")

    database_url: str = "sqlite:///data/muni.db"
    data_dir: Path = Path("data")
    # "anthropic" (best quality) or "nvidia" (free hosted models on build.nvidia.com)
    llm_provider: str = "anthropic"
    extraction_model: str = "claude-opus-4-8"
    translation_model: str = "claude-sonnet-5"
    nvidia_model: str = "meta/llama-3.3-70b-instruct"
    # Second extraction pass for cross-run agreement (doubles API cost per document).
    double_run: bool = True


def get_settings() -> Settings:
    return Settings()
