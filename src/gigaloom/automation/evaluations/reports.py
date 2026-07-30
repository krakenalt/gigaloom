"""Reports for the evals subcontext."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from typing import TYPE_CHECKING, Any, Mapping
from gigaloom.execution import ExecutionTransport
from gigaloom.automation.ports import HarnessRun
from gigaloom.automation.ports import redact_for_storage
from gigaloom.types import (
    GigaChatApiMode,
    HarnessCapability,
    spec_capability_values,
)
from .checks import (
    _case_harnesses as _case_harnesses,
    _selected_harnesses as _selected_harnesses,
)
from .codec import (
    _mapping as _mapping,
    _optional_text as _optional_text,
    _redacted_text as _redacted_text,
    eval_spec_to_dict as eval_spec_to_dict,
)
from .constants import (
    ADAPTER_COMPATIBILITY_CHECKS as ADAPTER_COMPATIBILITY_CHECKS,
    DEFAULT_EVAL_HARNESSES as DEFAULT_EVAL_HARNESSES,
    MAX_EVAL_REPETITIONS as MAX_EVAL_REPETITIONS,
    PROTOCOL_CONFORMANCE_FIXTURES as PROTOCOL_CONFORMANCE_FIXTURES,
)
from .models import (
    EvalCaseRunResult as EvalCaseRunResult,
    EvalCaseSpec as EvalCaseSpec,
    HarnessEvalRun as HarnessEvalRun,
    HarnessEvalSpec as HarnessEvalSpec,
)
from .primitives import positive_int as _positive_int

if TYPE_CHECKING:
    from .store import FilesystemHarnessEvalStore


def compare_eval_run_to_baseline(
    eval_run: HarnessEvalRun, baseline: Mapping[str, Any] | None
) -> Mapping[str, Any] | None:
    """Return stable pass-rate and normalized metric deltas."""
    if baseline is None:
        return None
    baseline_summary = _mapping(baseline.get("summary"))
    current_metrics = _mapping(eval_run.summary.get("metrics"))
    baseline_metrics = _mapping(baseline_summary.get("metrics"))
    metric_keys = sorted(set(current_metrics) | set(baseline_metrics))
    return {
        "baseline_eval_run_id": baseline.get("eval_run_id"),
        "git_sha": baseline.get("git_sha"),
        "config_hash": baseline.get("config_hash"),
        "api_mode": baseline.get("api_mode"),
        "adapter_dimensions": baseline.get("adapter_dimensions", []),
        "dimensions_match": (
            baseline.get("api_mode") == eval_run.api_mode.value
            and list(baseline.get("adapter_dimensions", ()))
            == list(eval_run.metadata.get("adapter_dimensions", ()))
        ),
        "score_delta": round(
            float(eval_run.summary.get("score") or 0.0)
            - float(baseline_summary.get("score") or 0.0),
            6,
        ),
        "metric_deltas": {
            key: round(
                float(current_metrics.get(key) or 0.0)
                - float(baseline_metrics.get(key) or 0.0),
                6,
            )
            for key in metric_keys
        },
    }


def eval_compatibility_matrix(
    spec: HarnessEvalSpec, registry: Any
) -> list[dict[str, Any]]:
    """Build only capability-valid case/harness cells for the quality matrix."""
    selected = _selected_harnesses(spec.harnesses or DEFAULT_EVAL_HARNESSES)
    cells: list[dict[str, Any]] = []
    for case in spec.cases:
        for harness_id in _case_harnesses(case, selected, False):
            harness_spec = registry.get(harness_id).spec()
            capabilities = spec_capability_values(harness_spec)
            required = (
                case.required_capability.value
                if case.required_capability is not None
                else None
            )
            if required is not None and required not in capabilities:
                continue
            cells.append(
                {
                    "case_id": case.id,
                    "harness_id": harness_id,
                    "required_capability": required,
                    "capabilities": list(capabilities),
                    "api_mode": spec.api_mode.value,
                    "compatible": True,
                }
            )
    return cells


def adapter_compatibility_matrix(
    registry: Any,
    *,
    api_modes: tuple[GigaChatApiMode, ...] = (
        GigaChatApiMode.V1,
        GigaChatApiMode.V2,
    ),
) -> list[dict[str, Any]]:
    """Describe declared adapter quality cells without claiming live evidence."""
    cells: list[dict[str, Any]] = []
    for harness in registry.list():
        spec = harness.spec()
        if HarnessCapability.AGENT_CLI not in spec.capabilities:
            continue
        claims = spec.adapter_capabilities
        native_resume = claims.get("native_resume")
        managed_config = claims.get("managed_mcp_headless")
        support = {
            "start": True,
            "stream": spec.supports_streaming and spec.supports_structured_events,
            "tool_lifecycle": spec.supports_structured_events,
            "failure": spec.supports_structured_events,
            "cancel": spec.supports_cancellation,
            "resume": (
                native_resume is not None
                and native_resume.status.value != "unsupported"
            ),
            "attachments": spec.supports_attachments,
            "managed_config": (
                managed_config is not None
                and managed_config.status.value == "supported"
            ),
        }
        for api_mode in api_modes:
            for check in ADAPTER_COMPATIBILITY_CHECKS:
                cells.append(
                    {
                        "harness_id": spec.id,
                        "api_mode": api_mode.value,
                        "check": check,
                        "supported": bool(support[check]),
                        "measured": False,
                        "evidence": "declared_adapter_contract",
                        "measurement": (
                            "run_provenance.gigachat_compatibility"
                            if check
                            in {
                                "start",
                                "stream",
                                "tool_lifecycle",
                                "failure",
                                "cancel",
                            }
                            else None
                        ),
                    }
                )
    return cells


def protocol_conformance_matrix(registry: Any) -> list[dict[str, Any]]:
    """Project protocol fixtures through real harness capabilities and routes."""
    cells: list[dict[str, Any]] = []
    for fixture_id, capability in PROTOCOL_CONFORMANCE_FIXTURES:
        compatible = [
            harness.spec().id
            for harness in registry.list()
            if capability.value in spec_capability_values(harness.spec())
        ]
        for api_mode in (GigaChatApiMode.V1, GigaChatApiMode.V2):
            cells.append(
                {
                    "fixture_id": fixture_id,
                    "required_capability": capability.value,
                    "api_mode": api_mode.value,
                    "compatible_harness_ids": compatible,
                    "runnable": bool(compatible),
                }
            )
    return cells


def _result_identity(result: EvalCaseRunResult) -> tuple[str, str, str, int]:
    return (
        result.case_id,
        result.target_type,
        result.target_id or result.harness_id,
        result.repetition,
    )


def _bounded_repetitions(value: Any) -> int:
    parsed = _positive_int(value, 1)
    if parsed > MAX_EVAL_REPETITIONS:
        raise ValueError(f"repetitions must be <= {MAX_EVAL_REPETITIONS}")
    return parsed


def _eval_evidence_binding(
    eval_run: HarnessEvalRun,
    *,
    case: EvalCaseSpec,
    harness_id: str,
    repetition: int,
) -> dict[str, Any]:
    checks = [
        {
            "type": check.type,
            "value": check.value,
            "name": check.name,
            "case_sensitive": check.case_sensitive,
        }
        for check in case.checks
    ]
    payload = {
        "schema_version": 1,
        "eval_run_id": eval_run.id,
        "config_hash": _eval_config_hash(eval_run),
        "case_id": case.id,
        "harness_id": harness_id,
        "repetition": repetition,
        "prompt_sha256": _canonical_hash(redact_for_storage(case.prompt)),
        "checks_sha256": _canonical_hash(redact_for_storage(checks)),
    }
    return {**payload, "binding_hash": _canonical_hash(payload)}


def _validated_eval_evidence_binding(
    eval_store: FilesystemHarnessEvalStore,
    *,
    payload: Mapping[str, Any],
    run: HarnessRun,
) -> Mapping[str, Any]:
    extra = _mapping(payload.get("extra"))
    binding = _mapping(extra.get("eval_evidence_binding"))
    if not binding:
        if payload.get("execution_transport") != "native_structured":
            return {
                "schema_version": 0,
                "status": "legacy_unbound",
                "eval_run_id": _optional_text(extra.get("eval_run_id")),
                "case_id": _optional_text(extra.get("eval_case_id")),
                "harness_id": run.harness_id,
                "repetition": _positive_int(extra.get("eval_repetition"), 1),
            }
        raise ValueError("eval evidence binding is missing")
    supplied_hash = _optional_text(binding.get("binding_hash"))
    canonical = {key: binding[key] for key in binding if key != "binding_hash"}
    if supplied_hash != _canonical_hash(canonical):
        raise ValueError("eval evidence binding hash mismatch")
    expected = {
        "eval_run_id": _optional_text(extra.get("eval_run_id")),
        "case_id": _optional_text(extra.get("eval_case_id")),
        "harness_id": run.harness_id,
        "repetition": _positive_int(extra.get("eval_repetition"), 1),
        "prompt_sha256": _canonical_hash(
            redact_for_storage(str(payload.get("prompt") or ""))
        ),
        "checks_sha256": _canonical_hash(
            redact_for_storage(list(extra.get("eval_checks") or ()))
        ),
    }
    changed = sorted(
        key for key, value in expected.items() if binding.get(key) != value
    )
    if changed:
        raise ValueError("eval evidence changed: " + ", ".join(changed))
    eval_run = eval_store.get_any(str(binding["eval_run_id"]))
    if binding.get("config_hash") != _eval_config_hash(eval_run):
        raise ValueError("eval config evidence changed after submission")
    return binding


def _eval_result_provenance(
    run: HarnessRun,
    *,
    evidence_binding: Mapping[str, Any],
    output_text: str,
) -> dict[str, Any]:
    link = _mapping(run.metadata.get("structured_session_link"))
    return {
        "source_evidence": dict(evidence_binding),
        "evaluator_input": {
            "run_id": run.id,
            "output_sha256": _canonical_hash(_redacted_text(output_text) or ""),
        },
        "native_session": (
            {
                "harness_session_id": run.session_id,
                "structured_session_link_id": link.get("id"),
                "structured_session_link_hash": link.get("link_hash"),
                "external_session_id": link.get("external_session_id"),
            }
            if link
            else None
        ),
    }


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _spec_config_hash(
    spec: HarnessEvalSpec,
    harness_ids: tuple[str, ...],
    repetitions: int,
    *,
    api_mode: GigaChatApiMode | None = None,
    adapter_dimensions: list[dict[str, Any]] | None = None,
    execution_transport: ExecutionTransport | None = None,
) -> str:
    payload = {
        "spec": eval_spec_to_dict(spec),
        "harness_ids": harness_ids,
        "repetitions": repetitions,
        "api_mode": (api_mode or spec.api_mode).value,
        "adapter_dimensions": adapter_dimensions or [],
    }
    if execution_transport is not None:
        payload["execution_transport"] = execution_transport.value
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _adapter_eval_dimensions(
    registry: Any,
    harness_ids: tuple[str, ...],
    api_mode: GigaChatApiMode,
) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []
    for harness_id in harness_ids:
        harness = registry.get(harness_id)
        capability_probe = getattr(harness, "capability_probe", None)
        snapshot = capability_probe() if callable(capability_probe) else None
        dimensions.append(
            {
                "harness_id": harness_id,
                "api_mode": api_mode.value,
                "binary_version": (
                    snapshot.parsed_version or snapshot.version
                    if snapshot is not None
                    else None
                ),
                "event_schema": (
                    snapshot.event_schema if snapshot is not None else None
                ),
                "native_event_schema": (
                    snapshot.native_event_schema if snapshot is not None else None
                ),
                "native_structured_events": (
                    snapshot.native_structured_events if snapshot is not None else False
                ),
            }
        )
    return dimensions


def _eval_config_hash(eval_run: HarnessEvalRun) -> str:
    value = eval_run.metadata.get("config_hash")
    if isinstance(value, str) and value:
        return value
    payload = {
        "spec": eval_run.spec_name,
        "model": eval_run.model,
        "api_mode": eval_run.api_mode.value,
        "mode": eval_run.mode,
        "harness_ids": eval_run.harness_ids,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _git_sha(project_root: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        return None
    return value
