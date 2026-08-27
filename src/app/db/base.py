"""Declarative metadata for the shared application PostgreSQL schema."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for the V2 PostgreSQL schema."""
