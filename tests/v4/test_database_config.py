from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine

from app.config import DEFAULT_KNOWLEDGE_DATABASE_URL, KnowledgeSettings
from app.db.session import create_app_engine, create_knowledge_engine

APP_URL = "postgresql+psycopg://app_user:app_secret@127.0.0.1:5433/app_db"
KNOWLEDGE_URL = "postgresql+psycopg://knowledge_app:knowledge_secret@127.0.0.1:5433/knowledge_db"


def test_app_database_url_wins_when_both_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DATABASE_URL", APP_URL)
    monkeypatch.setenv("KNOWLEDGE_DATABASE_URL", KNOWLEDGE_URL)

    settings = KnowledgeSettings(_env_file=None)

    assert settings.app_database_url is not None
    assert settings.app_database_url.get_secret_value() == APP_URL
    assert settings.knowledge_database_url.get_secret_value() == KNOWLEDGE_URL
    assert settings.database_url.get_secret_value() == APP_URL


def test_database_url_falls_back_to_knowledge_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("KNOWLEDGE_DATABASE_URL", KNOWLEDGE_URL)

    settings = KnowledgeSettings(_env_file=None)

    assert settings.app_database_url is None
    assert settings.database_url.get_secret_value() == KNOWLEDGE_URL
    assert settings.knowledge_database_url.get_secret_value() == KNOWLEDGE_URL


def test_existing_v2_knowledge_configuration_remains_compatible() -> None:
    settings = KnowledgeSettings(_env_file=None)

    assert settings.app_database_url is None
    assert settings.knowledge_database_url.get_secret_value() == DEFAULT_KNOWLEDGE_DATABASE_URL
    assert settings.database_url.get_secret_value() == DEFAULT_KNOWLEDGE_DATABASE_URL
    assert settings.v4_confirmation_challenge_ttl_seconds == 600
    assert settings.v4_confirmed_ttl_seconds == 600
    assert settings.v4_execution_lease_ttl_seconds == 60
    assert settings.v4_action_ttl_seconds == 1800


def test_empty_app_database_url_falls_back_to_knowledge_url() -> None:
    settings = KnowledgeSettings(
        app_database_url=SecretStr(""),
        knowledge_database_url=SecretStr(KNOWLEDGE_URL),
        _env_file=None,
    )

    assert settings.database_url.get_secret_value() == KNOWLEDGE_URL


def test_engine_uses_resolved_app_database_url() -> None:
    settings = KnowledgeSettings(
        app_database_url=SecretStr(APP_URL),
        knowledge_database_url=SecretStr(KNOWLEDGE_URL),
        _env_file=None,
    )
    engine = Mock(spec=Engine)

    with patch("app.db.session.create_engine", return_value=engine) as create_engine:
        result = create_knowledge_engine(settings)

    assert result is engine
    assert create_app_engine is create_knowledge_engine
    create_engine.assert_called_once_with(APP_URL, pool_pre_ping=True)
