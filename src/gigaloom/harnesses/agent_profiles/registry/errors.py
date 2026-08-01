"""Typed failures for inert ACP Registry reads."""


class RegistryError(RuntimeError):
    """Base ACP Registry failure."""


class RegistrySchemaError(RegistryError, ValueError):
    """The remote or cached document violates the supported registry schema."""


class RegistryCacheError(RegistryError):
    """Cached registry state is corrupt, unsafe, or internally inconsistent."""


class RegistryNetworkError(RegistryError):
    """The exact registry resource could not be fetched."""


class RegistryResponseError(RegistryError):
    """The registry returned an unsupported HTTP response."""


class RegistryUnavailableError(RegistryError):
    """Neither the network nor a valid cached snapshot can satisfy the read."""
