"""Safe domain failures for V2 source ingestion."""


class IngestionError(RuntimeError):
    """Base class for controlled ingestion failures."""


class SourceDocumentError(IngestionError):
    """Raised when Markdown or front matter cannot form a valid source document."""


class MetadataValidationError(SourceDocumentError):
    """Raised when source-controlled policy metadata is invalid."""


class ChunkingError(IngestionError):
    """Raised when normalized Markdown cannot be chunked safely."""


class IngestionConflictError(IngestionError):
    """Raised when immutable document identity conflicts with stored source content."""


class EmbeddingProfileMismatchError(IngestionError):
    """Raised when stored vectors use a different explicit embedding/index profile."""


class SupersessionError(IngestionError):
    """Raised when an explicit policy supersession transition is invalid."""


class IngestionPersistenceError(IngestionError):
    """Raised when PostgreSQL cannot atomically persist prepared ingestion data."""
