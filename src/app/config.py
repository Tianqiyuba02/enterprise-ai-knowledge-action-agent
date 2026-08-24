"""Environment-based configuration for the V0 application."""

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""

    def __init__(self) -> None:
        super().__init__(
            "Configuration is missing or invalid. Set GEMINI_API_KEY in the environment "
            "or in a local .env file."
        )


class Settings(BaseSettings):
    """Validated V0 settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    gemini_api_key: SecretStr = Field(
        validation_alias="GEMINI_API_KEY",
        min_length=1,
    )
    gemini_model: str = Field(
        default="gemini-3.5-flash",
        validation_alias="GEMINI_MODEL",
        min_length=1,
    )
    gemini_timeout_seconds: int = Field(
        default=30,
        validation_alias="GEMINI_TIMEOUT_SECONDS",
        ge=1,
        le=120,
    )
    gemini_max_attempts: int = Field(
        default=2,
        validation_alias="GEMINI_MAX_ATTEMPTS",
        ge=1,
        le=3,
    )


def load_settings() -> Settings:
    """Load settings while replacing validation details with a safe CLI message."""

    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError from exc
