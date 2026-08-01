"""Persistence-safe run and Workbench metadata projections."""

from __future__ import annotations

from typing import Any, Mapping


def agent_run_metadata(options: Mapping[str, Any]) -> dict[str, Any]:
    """Return immutable, redacted AgentProfile identity for run history."""
    agent_id = _optional_text(options.get("agent_id"))
    snapshot = options.get("agent_profile_snapshot")
    if agent_id is None or not isinstance(snapshot, Mapping):
        return {}
    metadata = {
        "agent_id": agent_id,
        "agent_profile_snapshot": dict(snapshot),
    }
    execution_plan = options.get("agent_execution_plan")
    if isinstance(execution_plan, Mapping):
        metadata["agent_execution_plan"] = dict(execution_plan)
    return metadata


def workbench_admission_run_metadata(
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Retain the content-free product admission receipt on each run."""
    admission = _mapping(_mapping(options.get("extra")).get("workbench_admission"))
    if admission.get("schema_version") != 1:
        return {}
    return {"workbench_admission": dict(admission)}


def workbench_session_selection_metadata(
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Retain explicit intent and authority independently from the mode alias."""
    admission = _mapping(_mapping(options.get("extra")).get("workbench_admission"))
    if admission.get("schema_version") != 1:
        return {}
    diagnostics = _mapping(admission.get("diagnostics"))
    compatibility = _mapping(diagnostics.get("compatibility"))
    return {
        "workbench_selection": {
            "schema_version": 1,
            "kind": admission.get("kind"),
            "intent": admission.get("intent"),
            "authority": admission.get("authority"),
            "input_source": admission.get("input_source"),
            "compatibility_warning": compatibility.get("warning"),
        }
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "agent_run_metadata",
    "workbench_admission_run_metadata",
    "workbench_session_selection_metadata",
]
