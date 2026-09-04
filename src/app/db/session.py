"""Lazy synchronous engine and session construction for V2 knowledge paths."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings


def create_knowledge_engine(settings: KnowledgeSettings | None = None) -> Engine:
    """Create an engine without connecting until a V2 database operation uses it."""

    resolved_settings = settings or load_knowledge_settings()
    database_url = resolved_settings.database_url.get_secret_value()
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    pool_options: dict[str, int] = {}
    if (
        resolved_settings.database_pool_size,
        resolved_settings.database_max_overflow,
        resolved_settings.database_pool_timeout_seconds,
    ) != (5, 2, 10):
        pool_options = {
            "pool_size": resolved_settings.database_pool_size,
            "max_overflow": resolved_settings.database_max_overflow,
            "pool_timeout": resolved_settings.database_pool_timeout_seconds,
        }
    return create_engine(database_url, pool_pre_ping=True, **pool_options)


def create_knowledge_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build an explicit synchronous session factory for V2 callers."""

    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


create_app_engine = create_knowledge_engine
create_app_session_factory = create_knowledge_session_factory
