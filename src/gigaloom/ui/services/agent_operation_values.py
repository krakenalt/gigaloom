"""Value normalization shared by managed-agent Web operations."""

from datetime import UTC, datetime

from gigaloom.contracts.operational_validation import validate_identity


def operation_failure_reason(error: Exception) -> str:
    """Return one safe reason identifier for an operation failure."""
    value = getattr(error, "reason_code", None)
    if isinstance(value, str):
        try:
            return validate_identity(value, field_name="installation failure reason")
        except ValueError:
            pass
    return f"{error.__class__.__name__.lower()}_during_agent_operation"


def operation_timestamp(value: datetime) -> str:
    """Normalize one timezone-aware operation timestamp to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("agent installation Web clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def optional_boolean(value: object) -> bool | None:
    """Validate one nullable boolean decoded from operation storage."""
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("agent installation operation value must be boolean")
