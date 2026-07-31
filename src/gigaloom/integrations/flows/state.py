# ruff: noqa: E402, F401, F403, F405
"""Integration flow persistence and validation helpers."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .models import *  # noqa: F403


def _validate_configuration(configuration: Mapping[str, Any]) -> None:
    if len(configuration) > MAX_CONFIGURATION_FIELDS:
        raise ValueError("integration configuration has too many fields")
    for key, value in configuration.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValueError("integration configuration field is invalid")
        if _SENSITIVE_FIELD_RE.search(key) and not key.endswith("_env_var"):
            raise ValueError(
                "integration configuration accepts references, not secrets"
            )
        _json_value(value)


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ValueError("integration payload nesting is too deep")
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 4096:
            raise ValueError("integration payload text is too long")
        return value
    if isinstance(value, Mapping):
        if len(value) > 128:
            raise ValueError("integration payload object is too large")
        return {
            str(key): _json_value(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 256:
            raise ValueError("integration payload list is too large")
        return [_json_value(item, depth=depth + 1) for item in value]
    raise ValueError("integration payload must contain JSON values only")


def _private_record_to_dict(record: IntegrationFlowRecord) -> dict[str, Any]:
    from .projection import integration_flow_record_to_dict

    return {
        **integration_flow_record_to_dict(record),
        "workspace": record.workspace,
        "request": record.request,
        "receipt_id": record.receipt_id,
    }


def _record_from_dict(payload: object) -> IntegrationFlowRecord:
    from .projection import _normalize_request

    if not isinstance(payload, Mapping):
        raise IntegrationFlowError("integration flow record is invalid")
    try:
        source_provenance = payload.get("source_provenance")
        if source_provenance is not None and not isinstance(source_provenance, Mapping):
            raise TypeError("source provenance must be an object")
        record = IntegrationFlowRecord(
            id=str(payload["id"]),
            plan_id=str(payload["plan_id"]),
            status=IntegrationFlowStatus(str(payload["status"])),
            source=IntegrationFlowSource(str(payload["source"])),
            package_id=str(payload["package_id"]),
            package_version=str(payload["package_version"]),
            manifest_sha256=str(payload["manifest_sha256"]),
            source_provenance=(
                _json_value(source_provenance)
                if source_provenance is not None
                else None
            ),
            target_id=str(payload["target_id"]),
            scope=InstallationScope(str(payload["scope"])),
            workspace=(str(payload["workspace"]) if payload.get("workspace") else None),
            request=_normalize_request(payload["request"]),
            receipt_id=(
                str(payload["receipt_id"]) if payload.get("receipt_id") else None
            ),
            verification_status=str(payload["verification_status"]),
            rollback_available=bool(payload["rollback_available"]),
            error_code=(
                str(payload["error_code"]) if payload.get("error_code") else None
            ),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            events=tuple(
                IntegrationFlowEvent(
                    stage=str(item["stage"]),
                    status=str(item["status"]),
                    occurred_at=str(item["occurred_at"]),
                    code=str(item["code"]) if item.get("code") else None,
                )
                for item in payload["events"]
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrationFlowError("integration flow record is invalid") from exc
    _validate_flow_id(record.id)
    _validate_plan_id(record.plan_id)
    return record


def _json_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_flow_id(value: str) -> None:
    if _FLOW_ID_RE.fullmatch(value) is None:
        raise ValueError("integration flow id is invalid")


def _validate_plan_id(value: str) -> None:
    if _PLAN_ID_RE.fullmatch(value) is None:
        raise ValueError("integration plan id is invalid")


def _validate_identity(value: str, *, field_name: str) -> None:
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


__all__ = [name for name in globals() if not name.startswith("__")]
