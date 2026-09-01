class KnowledgePlatformError(Exception):
    """Base error for expected application failures."""


class AuthorizationError(KnowledgePlatformError):
    """The authenticated principal lacks the required role."""


class AuthenticationError(KnowledgePlatformError):
    """The request does not contain a valid identity."""


class IngestionConflictError(KnowledgePlatformError):
    """A document version was reused with different content."""


class TransientProviderError(KnowledgePlatformError):
    """A dependency failed in a way that can safely be retried."""


class PermanentProviderError(KnowledgePlatformError):
    """A dependency rejected the request and retrying will not help."""


class StructuredOutputError(KnowledgePlatformError):
    """A model response failed schema or grounding validation."""
