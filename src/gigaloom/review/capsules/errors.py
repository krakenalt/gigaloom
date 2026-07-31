"""Typed failures for Run Capsule construction and verification."""


class CapsuleError(ValueError):
    """Base error for invalid or unsafe Run Capsule data."""


class CapsuleSchemaError(CapsuleError):
    """Raised when a capsule document violates its frozen schema."""


class CapsuleIntegrityError(CapsuleError):
    """Raised when a capsule digest or signature does not verify."""


class CapsuleArchiveError(CapsuleError):
    """Raised when an archive is malformed, unsafe, or exceeds bounds."""


class CapsuleCheckoutError(CapsuleError):
    """Raised when an operator-supplied checkout cannot be verified safely."""
