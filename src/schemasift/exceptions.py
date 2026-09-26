class SchemaSiftError(Exception):
    """Base exception."""


class ProviderError(SchemaSiftError):
    """A provider failed or returned an invalid response."""


class BudgetExceeded(SchemaSiftError):
    """A configured hard budget would be exceeded."""


class ConfigurationError(SchemaSiftError):
    """Configuration is missing or invalid."""

