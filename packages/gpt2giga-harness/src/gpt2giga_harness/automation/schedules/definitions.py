"""Definitions for scheduled automation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dateutil import rrule, tz
import yaml
from gpt2giga_harness.automation.agents.api import (
    agent_profile_to_dict,
    load_agent_profile,
)
from gpt2giga_harness.automation.evaluations.api import (
    eval_spec_to_dict,
    load_eval_spec,
)
from gpt2giga_harness.projects.api import (
    HarnessProject,
    load_project_config,
    project_preset_to_dict,
)
from gpt2giga_harness.automation.ports import exclusive_file_lock
from gpt2giga_harness.automation.workflows.api import (
    load_workflow,
    workflow_definition_to_dict,
)
from .constants import (
    DEFAULT_PREVIEW_COUNT as DEFAULT_PREVIEW_COUNT,
    SCHEDULE_DIRECTORY as SCHEDULE_DIRECTORY,
)
from .models import (
    ScheduleConflictError as ScheduleConflictError,
    ScheduleDefinition as ScheduleDefinition,
    ScheduleError as ScheduleError,
    ScheduleOccurrence as ScheduleOccurrence,
)


def build_schedule_definition(
    project: HarnessProject, payload: Mapping[str, Any]
) -> ScheduleDefinition:
    """Validate user input and capture an immutable target snapshot."""
    schedule_id = _safe_id(payload.get("id"), "schedule id")
    target = _mapping(payload.get("target"))
    target_kind = str(target.get("kind") or "").strip().lower()
    target_id = _safe_id(target.get("id"), "target id")
    snapshot = _target_snapshot(project, target_kind, target_id)
    target_hash = _hash(snapshot)
    cadence = _mapping(payload.get("cadence"))
    cadence_kind = str(cadence.get("kind") or "").strip().lower()
    if cadence_kind not in {"once", "interval", "rrule"}:
        raise ScheduleError("cadence.kind must be once, interval, or rrule")
    timezone_name = str(cadence.get("timezone") or "").strip()
    _timezone(timezone_name)
    start_at = _local_datetime_text(cadence.get("start_at"), timezone_name)
    interval_seconds = _optional_positive(cadence.get("interval_seconds"))
    rrule_text = _optional_text(cadence.get("rrule"))
    if cadence_kind == "interval" and interval_seconds is None:
        raise ScheduleError("interval cadence requires interval_seconds")
    if cadence_kind == "rrule" and rrule_text is None:
        raise ScheduleError("rrule cadence requires rrule")
    if rrule_text:
        try:
            rrule.rrulestr(rrule_text, dtstart=datetime.fromisoformat(start_at))
        except (ValueError, TypeError) as exc:
            raise ScheduleError(f"Invalid RRULE: {exc}") from exc
    destination = str(payload.get("destination") or "new_task").strip().lower()
    session_id = _optional_text(payload.get("session_id"))
    if destination not in {"new_task", "resume"}:
        raise ScheduleError("destination must be new_task or resume")
    if destination == "resume" and not session_id:
        raise ScheduleError("resume destination requires session_id")
    workspace_policy = str(payload.get("workspace_policy") or "worktree")
    if workspace_policy != "worktree":
        raise ScheduleError("scheduled work must use dedicated worktree isolation")
    definition = ScheduleDefinition(
        id=schedule_id,
        title=str(payload.get("title") or schedule_id).strip(),
        target_kind=target_kind,
        target_id=target_id,
        target_hash=target_hash,
        target_snapshot=snapshot,
        cadence_kind=cadence_kind,
        timezone=timezone_name,
        start_at=start_at,
        interval_seconds=interval_seconds,
        rrule_text=rrule_text,
        prompt=_optional_text(payload.get("prompt")),
        inputs=dict(_mapping(payload.get("inputs"))),
        destination=destination,
        session_id=session_id,
        workspace_policy=workspace_policy,
        timeout_seconds=_positive(payload.get("timeout_seconds"), 3600.0),
        max_attempts=max(int(payload.get("max_attempts") or 1), 1),
        overlap_policy=str(payload.get("overlap_policy") or "skip"),
        max_concurrency=max(int(payload.get("max_concurrency") or 1), 1),
        misfire_policy=str(payload.get("misfire_policy") or "skip"),
        misfire_grace_seconds=_positive(payload.get("misfire_grace_seconds"), 60.0),
        notifications={
            "desktop": bool(_mapping(payload.get("notifications")).get("desktop"))
        },
    )
    if definition.overlap_policy not in {"skip", "allow"}:
        raise ScheduleError("overlap_policy must be skip or allow")
    if definition.misfire_policy not in {"skip", "run_once"}:
        raise ScheduleError("misfire_policy must be skip or run_once")
    return replace(
        definition,
        source_hash=_hash(schedule_definition_to_dict(definition, include_hash=False)),
    )


def save_schedule(
    project: HarnessProject,
    definition: ScheduleDefinition,
    *,
    expected_hash: str | None = None,
) -> Path:
    """Atomically persist one shareable schedule definition."""
    directory = Path(project.root) / SCHEDULE_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{definition.id}.yaml"
    with exclusive_file_lock(path):
        if expected_hash is not None:
            try:
                actual_hash = load_schedule(project.root, definition.id).source_hash
            except KeyError:
                actual_hash = None
            if actual_hash != expected_hash:
                raise ScheduleConflictError("Schedule changed since it was loaded")
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp.write_text(
            yaml.safe_dump(
                schedule_definition_to_dict(definition, include_hash=False),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        temp.replace(path)
    return path


def load_schedule(project_root: str | Path, schedule_id: str) -> ScheduleDefinition:
    """Load and validate one schedule, retaining its captured target snapshot."""
    safe_id = _safe_id(schedule_id, "schedule id")
    path = Path(project_root) / SCHEDULE_DIRECTORY / f"{safe_id}.yaml"
    if not path.is_file():
        raise KeyError(safe_id)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScheduleError(f"Invalid schedule YAML: {path.name}") from exc
    return _definition_from_saved(_mapping(data))


def discover_schedules(project_root: str | Path) -> tuple[ScheduleDefinition, ...]:
    """Return valid schedule definitions in stable id order."""
    directory = Path(project_root) / SCHEDULE_DIRECTORY
    if not directory.is_dir():
        return ()
    return tuple(
        load_schedule(project_root, path.stem)
        for path in sorted(directory.glob("*.yaml"))
    )


def next_occurrences(
    definition: ScheduleDefinition,
    *,
    after: datetime | None = None,
    count: int = DEFAULT_PREVIEW_COUNT,
) -> tuple[dict[str, Any], ...]:
    """Compute future UTC instants with explicit DST skip/fold semantics."""
    after_utc = _aware_utc(after or datetime.now(timezone.utc))
    zone = _timezone(definition.timezone)
    local_after = after_utc.astimezone(zone).replace(tzinfo=None)
    start = datetime.fromisoformat(definition.start_at)
    candidates: list[datetime] = []
    if definition.cadence_kind == "once":
        candidates = [start]
    elif definition.cadence_kind == "interval":
        seconds = float(definition.interval_seconds or 0)
        if seconds <= 0:
            return ()
        elapsed = max((local_after - start).total_seconds(), 0.0)
        step = int(elapsed // seconds) + 1 if local_after >= start else 0
        candidates = [
            start + timedelta(seconds=seconds * (step + i)) for i in range(count * 3)
        ]
    else:
        rule = rrule.rrulestr(definition.rrule_text or "", dtstart=start)
        cursor = local_after
        for _ in range(count * 3):
            item = rule.after(cursor, inc=False)
            if item is None:
                break
            candidates.append(item.replace(tzinfo=None))
            cursor = item
    result: list[dict[str, Any]] = []
    dateutil_zone = tz.gettz(definition.timezone)
    for local_value in candidates:
        aware = local_value.replace(tzinfo=dateutil_zone)
        if not tz.datetime_exists(aware):
            result.append(
                {
                    "local": local_value.isoformat(),
                    "utc": None,
                    "status": "misfire",
                    "reason": "nonexistent_local_time",
                }
            )
        else:
            if tz.datetime_ambiguous(aware):
                aware = tz.enfold(aware, fold=0)
            instant = aware.astimezone(timezone.utc)
            if instant > after_utc:
                result.append(
                    {
                        "local": local_value.isoformat(),
                        "utc": instant.isoformat(),
                        "status": "scheduled",
                        "reason": "ambiguous_first_instant"
                        if tz.datetime_ambiguous(aware)
                        else None,
                    }
                )
        if len(result) >= count:
            break
    return tuple(result)


def schedule_definition_to_dict(
    definition: ScheduleDefinition, *, include_hash: bool = True
) -> dict[str, Any]:
    payload = {
        "id": definition.id,
        "title": definition.title,
        "target": {
            "kind": definition.target_kind,
            "id": definition.target_id,
            "hash": definition.target_hash,
            "snapshot": dict(definition.target_snapshot),
        },
        "cadence": {
            "kind": definition.cadence_kind,
            "timezone": definition.timezone,
            "start_at": definition.start_at,
            "interval_seconds": definition.interval_seconds,
            "rrule": definition.rrule_text,
        },
        "prompt": definition.prompt,
        "inputs": dict(definition.inputs or {}),
        "destination": definition.destination,
        "session_id": definition.session_id,
        "workspace_policy": definition.workspace_policy,
        "timeout_seconds": definition.timeout_seconds,
        "max_attempts": definition.max_attempts,
        "overlap_policy": definition.overlap_policy,
        "max_concurrency": definition.max_concurrency,
        "misfire_policy": definition.misfire_policy,
        "misfire_grace_seconds": definition.misfire_grace_seconds,
        "notifications": dict(definition.notifications or {"desktop": False}),
    }
    if include_hash:
        payload["source_hash"] = definition.source_hash
    return payload


def occurrence_to_dict(item: ScheduleOccurrence) -> dict[str, Any]:
    return dict(item.__dict__)


def _definition_from_saved(data: Mapping[str, Any]) -> ScheduleDefinition:
    target, cadence = _mapping(data.get("target")), _mapping(data.get("cadence"))
    definition = ScheduleDefinition(
        id=_safe_id(data.get("id"), "schedule id"),
        title=str(data.get("title") or data.get("id")),
        target_kind=str(target.get("kind")),
        target_id=_safe_id(target.get("id"), "target id"),
        target_hash=str(target.get("hash") or ""),
        target_snapshot=dict(_mapping(target.get("snapshot"))),
        cadence_kind=str(cadence.get("kind")),
        timezone=str(cadence.get("timezone")),
        start_at=str(cadence.get("start_at")),
        interval_seconds=_optional_positive(cadence.get("interval_seconds")),
        rrule_text=_optional_text(cadence.get("rrule")),
        prompt=_optional_text(data.get("prompt")),
        inputs=dict(_mapping(data.get("inputs"))),
        destination=str(data.get("destination") or "new_task"),
        session_id=_optional_text(data.get("session_id")),
        workspace_policy=str(data.get("workspace_policy") or "worktree"),
        timeout_seconds=_positive(data.get("timeout_seconds"), 3600),
        max_attempts=max(int(data.get("max_attempts") or 1), 1),
        overlap_policy=str(data.get("overlap_policy") or "skip"),
        max_concurrency=max(int(data.get("max_concurrency") or 1), 1),
        misfire_policy=str(data.get("misfire_policy") or "skip"),
        misfire_grace_seconds=_positive(data.get("misfire_grace_seconds"), 60),
        notifications={
            "desktop": bool(_mapping(data.get("notifications")).get("desktop"))
        },
    )
    source_hash = _hash(schedule_definition_to_dict(definition, include_hash=False))
    if _hash(definition.target_snapshot) != definition.target_hash:
        raise ScheduleError("Persisted target snapshot hash mismatch")
    return replace(definition, source_hash=source_hash)


def _target_snapshot(
    project: HarnessProject, kind: str, target_id: str
) -> dict[str, Any]:
    if kind == "agent":
        return _jsonable(
            agent_profile_to_dict(load_agent_profile(project.root, target_id))
        )
    if kind == "workflow":
        return _jsonable(
            workflow_definition_to_dict(load_workflow(project.root, target_id))
        )
    if kind == "eval":
        return _jsonable(eval_spec_to_dict(load_eval_spec(project.root, target_id)))
    if kind == "preset":
        config = load_project_config(project.root)
        try:
            return _jsonable(
                project_preset_to_dict(target_id, config.presets[target_id])
            )
        except KeyError as exc:
            raise ScheduleError(f"Preset not found: {target_id}") from exc
    raise ScheduleError("target.kind must be agent, preset, workflow, or eval")


def _first_scheduled(
    definition: ScheduleDefinition, after: datetime | None = None
) -> str | None:
    return next(
        (
            str(item["utc"])
            for item in next_occurrences(definition, after=after, count=8)
            if item["utc"]
        ),
        None,
    )


def _schedule_key(project_id: str, schedule_id: str) -> str:
    return hashlib.sha256(f"{project_id}\0{schedule_id}".encode()).hexdigest()


def _manual_occurrence_id(
    schedule_key: str,
    trigger: str,
    idempotency_key: str,
) -> str:
    key = str(idempotency_key or "").strip()
    if not key:
        raise ScheduleError("idempotency key is required")
    if len(key) > 200:
        raise ScheduleError("idempotency key must be at most 200 characters")
    identity = f"{schedule_key}\0{trigger}\0{key}"
    return f"occurrence_{hashlib.sha256(identity.encode()).hexdigest()}"


def _occurrence_from_row(row: Any) -> ScheduleOccurrence:
    return ScheduleOccurrence(
        **{key: row[key] for key in ScheduleOccurrence.__dataclass_fields__}
    )


def _local_datetime_text(value: Any, timezone_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return (
            datetime.now(_timezone(timezone_name))
            .replace(tzinfo=None, microsecond=0)
            .isoformat()
        )
    parsed = datetime.fromisoformat(text)
    return (
        parsed.astimezone(_timezone(timezone_name)).replace(tzinfo=None)
        if parsed.tzinfo
        else parsed
    ).isoformat()


def _timezone(name: str) -> ZoneInfo:
    if not name:
        raise ScheduleError("An explicit IANA timezone is required")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError(f"Unknown IANA timezone: {name}") from exc


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for char in text
    ):
        raise ScheduleError(
            f"{label} must contain only letters, digits, underscore, or hyphen"
        )
    return text


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_positive(value: Any) -> float | None:
    if value is None:
        return None
    return _positive(value, 0)


def _positive(value: Any, default: float) -> float:
    number = float(value if value is not None else default)
    if number <= 0:
        raise ScheduleError("numeric schedule limits must be positive")
    return number


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
