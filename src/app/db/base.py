"""Declarative metadata for V2 knowledge persistence."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for the V2 PostgreSQL schema."""
