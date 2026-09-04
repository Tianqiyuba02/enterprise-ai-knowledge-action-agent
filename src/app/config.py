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
    v4_confirmation_challenge_ttl_seconds: int = Field(
        default=600,
        validation_alias="V4_CONFIRMATION_CHALLENGE_TTL_SECONDS",
        ge=1,
        le=86_400,
    )
    v4_confirmed_ttl_seconds: int = Field(
        default=600,
        validation_alias="V4_CONFIRMED_TTL_SECONDS",
        ge=1,
        le=86_400,
    )
    v4_action_ttl_seconds: int = Field(
        default=1800,
        validation_alias="V4_ACTION_TTL_SECONDS",
        ge=1,
        le=86_400,
    )
    database_pool_size: int = Field(
        default=5,
        validation_alias="DATABASE_POOL_SIZE",
        ge=1,
        le=20,
    )
    database_max_overflow: int = Field(
        default=2,
        validation_alias="DATABASE_MAX_OVERFLOW",
        ge=0,
        le=20,
    )
    database_pool_timeout_seconds: int = Field(
        default=10,
        validation_alias="DATABASE_POOL_TIMEOUT_SECONDS",
        ge=1,
        le=60,
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


class PublicDemoSettings(BaseSettings):
    """M3 public-demo controls. Disabled by default for all V1-V5 local paths."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    enabled: bool = Field(default=False, validation_alias="PUBLIC_DEMO_MODE")
    internal_portal_key: SecretStr | None = Field(
        default=None,
        validation_alias="INTERNAL_PORTAL_KEY",
    )
    visitor_assistant_daily_limit: int = Field(
        default=8,
        validation_alias="DEMO_VISITOR_ASSISTANT_DAILY_LIMIT",
        ge=1,
    )
    visitor_action_daily_limit: int = Field(
        default=4,
        validation_alias="DEMO_VISITOR_ACTION_DAILY_LIMIT",
        ge=1,
    )
    visitor_revision_daily_limit: int = Field(
        default=8,
        validation_alias="DEMO_VISITOR_REVISION_DAILY_LIMIT",
        ge=1,
    )
    global_assistant_daily_limit: int = Field(
        default=60,
        validation_alias="DEMO_GLOBAL_ASSISTANT_DAILY_LIMIT",
        ge=1,
    )
    global_execution_daily_limit: int = Field(
        default=40,
        validation_alias="DEMO_GLOBAL_EXECUTION_DAILY_LIMIT",
        ge=1,
    )
    global_provider_daily_limit: int = Field(
        default=300,
        validation_alias="DEMO_GLOBAL_PROVIDER_DAILY_LIMIT",
        ge=1,
    )
    expected_document_count: int = Field(
        default=13,
        validation_alias="DEMO_EXPECTED_DOCUMENT_COUNT",
        ge=1,
    )
    expected_chunk_count: int = Field(
        default=47,
        validation_alias="DEMO_EXPECTED_CHUNK_COUNT",
        ge=1,
    )
    worker_stale_seconds: int = Field(
        default=20,
        validation_alias="DEMO_WORKER_STALE_SECONDS",
        ge=5,
        le=300,
    )
    assistant_deadline_seconds: int = Field(
        default=45,
        validation_alias="DEMO_ASSISTANT_DEADLINE_SECONDS",
        ge=5,
        le=60,
    )

    def require_internal_key(self) -> str:
        key = self.internal_portal_key
        value = key.get_secret_value() if key is not None else ""
        if self.enabled and len(value) < 24:
            raise KnowledgeConfigurationError
        return value


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


def load_public_demo_settings() -> PublicDemoSettings:
    """Load public-demo controls without enabling them implicitly."""

    try:
        settings = PublicDemoSettings()
        settings.require_internal_key()
        return settings
    except ValidationError as exc:
        raise KnowledgeConfigurationError from exc
