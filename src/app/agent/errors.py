"""Safe API-facing failure signals from the internal V3 agent result mapper."""


class AssistantModelError(RuntimeError):
    """Base class for bounded assistant-provider API failures."""


class AssistantModelUnavailableError(AssistantModelError):
    """Raised when the assistant provider is temporarily unavailable."""


class AssistantModelRateLimitedError(AssistantModelError):
    """Raised when the assistant provider is temporarily rate limited."""
