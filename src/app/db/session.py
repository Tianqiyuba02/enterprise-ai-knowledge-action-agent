"""Lazy synchronous engine and session construction for V2 knowledge paths."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings


def create_knowledge_engine(settings: KnowledgeSettings | None = None) -> Engine:
    """Create an engine without connecting until a V2 database operation uses it."""

    resolved_settings = settings or load_knowledge_settings()
    return create_engine(
        resolved_settings.knowledge_database_url.get_secret_value(),
        pool_pre_ping=True,
    )


def create_knowledge_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build an explicit synchronous session factory for V2 callers."""

    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
