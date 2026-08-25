"""Safe internal failures for V2 authority-aware retrieval."""


class KnowledgeRetrievalError(RuntimeError):
    """Base class for controlled Stage 3 retrieval failures."""


class InvalidKnowledgeQuestionError(KnowledgeRetrievalError):
    """Raised before provider work when a retrieval question is invalid."""


class InvalidQueryVectorError(KnowledgeRetrievalError):
    """Raised before SQL when a query vector does not match the index profile."""


class KnowledgeDatabaseError(KnowledgeRetrievalError):
    """Raised when PostgreSQL cannot complete a retrieval request."""
