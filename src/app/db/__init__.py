"""Synchronous V2 knowledge-database boundary."""

from app.db.base import Base
from app.db.models import Document, DocumentChunk

__all__ = ["Base", "Document", "DocumentChunk"]
