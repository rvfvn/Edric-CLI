"""Provider errors shared by working and scaffolded adapters."""


class ProviderError(RuntimeError):
    """A user-readable provider failure, without raw requests or credentials."""


class ProviderUnavailableError(ProviderError):
    """A provider cannot be used with the current setup."""
