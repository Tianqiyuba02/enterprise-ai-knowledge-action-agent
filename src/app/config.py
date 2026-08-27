"""Environment configuration with isolated released-app, V2, and V3 boundaries."""

from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

APPROVED_EMBEDDING_MODEL = "gemini-embedding-2"
APPROVED_EMBEDDING_DIMENSION = 768
APPROVED_GROUNDED_MODEL = "gemini-3.6-flash"
APPROVED_AGENT_MODEL = "gemini-3.6-flash"
DEFAULT_KNOWLEDGE_DATABASE_URL = (
    "postgresql+psycopg://knowledge_app:knowledge_app_local_only@127.0.0.1:5433/knowledge_agent"
)


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""

    def __init__(self) -> None:
        super().__init__(
            "Configuration is missing or invalid. Set GEMINI_API_KEY in the environment "
            "or in a local .env file."
        )


class KnowledgeConfigurationError(RuntimeError):
    """Raised only when a V2 knowledge path loads invalid database configuration."""

    def __init__(self) -> None:
        super().__init__("Knowledge database configuration is missing or invalid.")


class AgentConfigurationError(RuntimeError):
    """Raised only when a V3 agent path loads invalid model configuration."""

    def __init__(self) -> None:
        super().__init__("Agent configuration is missing or invalid.")


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


class KnowledgeSettings(BaseSettings):
    """V2 knowledge settings loaded lazily and independently of V1 application startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    app_database_url: SecretStr | None = Field(
        default=None,
        validation_alias="APP_DATABASE_URL",
    )
    knowledge_database_url: SecretStr = Field(
        default=DEFAULT_KNOWLEDGE_DATABASE_URL,
        validation_alias="KNOWLEDGE_DATABASE_URL",
        min_length=1,
    )
    knowledge_embedding_model: Literal["gemini-embedding-2"] = Field(
        default=APPROVED_EMBEDDING_MODEL,
        validation_alias="KNOWLEDGE_EMBEDDING_MODEL",
    )
    knowledge_embedding_dimension: Literal[768] = Field(
        default=APPROVED_EMBEDDING_DIMENSION,
        validation_alias="KNOWLEDGE_EMBEDDING_DIMENSION",
    )
    knowledge_grounded_model: Literal["gemini-3.6-flash"] = Field(
        default=APPROVED_GROUNDED_MODEL,
        validation_alias="KNOWLEDGE_GROUNDED_MODEL",
    )

    @property
    def database_url(self) -> SecretStr:
        """Prefer APP_DATABASE_URL, then fall back to KNOWLEDGE_DATABASE_URL."""

        if self.app_database_url is not None and self.app_database_url.get_secret_value():
            return self.app_database_url
        return self.knowledge_database_url


class AgentSettings(BaseSettings):
    """V3 settings loaded lazily and independently of released V1/V2 paths."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    agent_model: Literal["gemini-3.6-flash"] = Field(
        default=APPROVED_AGENT_MODEL,
        validation_alias="AGENT_MODEL",
    )
    agent_timeout_seconds: int = Field(
        default=60,
        validation_alias="AGENT_TIMEOUT_SECONDS",
        ge=1,
        le=120,
    )
    agent_max_attempts: int = Field(
        default=1,
        validation_alias="AGENT_MAX_ATTEMPTS",
        ge=1,
        le=3,
    )


def load_settings() -> Settings:
    """Load settings while replacing validation details with a safe CLI message."""

    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError from exc


def load_knowledge_settings() -> KnowledgeSettings:
    """Load isolated V2 settings only when a knowledge database path requests them."""

    try:
        return KnowledgeSettings()
    except ValidationError as exc:
        raise KnowledgeConfigurationError from exc


def load_agent_settings() -> AgentSettings:
    """Load isolated V3 model settings only when an agent path requests them."""

    try:
        return AgentSettings()
    except ValidationError as exc:
        raise AgentConfigurationError from exc
