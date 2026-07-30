"""Command-line interface for the gpt2giga Unified Harness."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping

import yaml

from gigaloom.adapter_scaffold import (
    render_adapter_module,
    scaffold_adapter_package,
)
from gigaloom.adapter_sdk import (
    adapter_conformance_report_to_dict,
    load_installed_conformance_subject,
    run_adapter_conformance,
)
from gigaloom.application import SessionApplicationService
from gigaloom.bootstrap import BootstrapService
from gigaloom.agents import (
    agent_profile_to_dict,
    agent_run_payload,
    discover_agent_profiles,
    load_agent_profile,
    parse_agent_profile,
)
from gigaloom.capability_matrix import (
    build_adapter_capability_matrix,
    render_adapter_capability_matrix_markdown,
    render_agent_surface_capability_matrix_markdown,
)
from gigaloom.cli_commands.output import print_json as _print_json
from gigaloom.cli_commands.parser import build_parser
from gigaloom.config import HarnessConfig
from gigaloom.completion import render_completion
from gigaloom.cli_capabilities import cli_capability_snapshot_to_dict
from gigaloom.diagnostics.compatibility.guardian import (
    run_compatibility_guardian,
)
from gigaloom.diagnostics.doctor.report import (
    build_doctor_report,
    format_doctor_report,
    write_doctor_support_report,
)
from gigaloom.editor import (
    build_open_diff_plan,
    build_open_file_plan,
    build_open_run_workspace_plan,
    build_open_terminal_plan,
    build_open_workspace_plan,
    editor_open_plan_to_dict,
    execute_editor_plan,
    workspace_for_run,
)
from gigaloom.execution import ExecutionTransport
from gigaloom.integration_flows import (
    IntegrationFlowService,
    integration_flow_record_to_dict,
)
from gigaloom.integration_groups import (
    GroupedIntegrationService,
    integration_group_record_to_dict,
)
from gigaloom.integration_scaffold import scaffold_integration_package
from gigaloom.integration_sdk import (
    integration_conformance_report_to_dict,
    load_extension_target_document,
    load_integration_package_document,
    run_integration_conformance,
)
from gigaloom.handoff_capsules import HandoffCapsuleService
from gigaloom.executables import (
    executable_resolution_to_dict,
    set_user_executable,
    unset_user_executable,
    user_config_path,
)
from gigaloom.evals import (
    FilesystemHarnessEvalStore,
    discover_eval_specs,
    eval_run_to_dict,
    eval_spec_load_error_to_dict,
    eval_spec_to_dict,
    load_eval_spec,
    run_eval,
)
from gigaloom.native import HarnessInvocationMode
from gigaloom.native.base import (
    discovery_error_to_dict,
    native_command_plan_to_dict,
)
from gigaloom.native.discovery import normalize_native_workspace
from gigaloom.native.models import (
    NativeSessionRef,
    NativeSessionStatus,
    execution_snapshot_to_dict,
)
from gigaloom.native.registry import (
    UnknownNativeHistoryConnectorError,
    create_default_native_registry,
)
from gigaloom.native.store import (
    FilesystemNativeSessionIndexStore,
    native_session_ref_to_dict,
)
from gigaloom.project import (
    init_project_config,
    load_project_config,
    project_config_path,
    project_config_to_dict,
    project_to_dict,
    render_project_preset,
    rendered_project_preset_to_dict,
    resolve_project,
)
from gigaloom.project_memory import (
    FilesystemProjectMemoryStore,
    memory_entry_to_dict,
)
from gigaloom.preflight import (
    build_preflight_report,
    format_preflight_block_message,
    preflight_report_to_dict,
)
from gigaloom.permission_simulator import build_permission_simulation
from gigaloom.diagnostics.performance.api import (
    run_performance_baseline,
    write_performance_report,
)
from gigaloom.pr_artifacts import build_pr_artifact, pr_artifact_to_dict
from gigaloom.plugins import (
    harness_validation_report_to_dict,
    validate_harness_spec,
)
from gigaloom.provenance import (
    build_replay_request,
    build_run_provenance,
    run_provenance_to_dict,
)
from gigaloom.readiness import build_execution_readiness
from gigaloom.reviewed_evidence import reviewed_evidence_manifest
from gigaloom.registry import UnknownHarnessError, create_default_registry
from gigaloom.runtime.models import job_to_dict
from gigaloom.runtime.payloads import DurableJobPayloadStore
from gigaloom.runtime.policy import (
    approval_request_to_dict,
)
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.runtime.worker import (
    DurableJobDispatcher,
)
from gigaloom.schedules import (
    ScheduleService,
    build_schedule_definition,
    next_occurrences,
    schedule_definition_to_dict,
)
from gigaloom.session_runner import HarnessSessionRunner
from gigaloom.sessions import (
    FilesystemHarnessSessionStore,
)
from gigaloom.sessions.models import (
    bundle_to_dict,
    event_to_dict,
    run_to_dict,
    session_to_dict,
)
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessNativeLink,
    HarnessStoredEvent,
    message_to_dict,
    native_link_to_dict,
)
from gigaloom.sessions.redaction import redact_for_storage
from gigaloom.sessions.store import new_id, utc_now
from gigaloom.session_titles import provider_native_title_metadata
from gigaloom.settings import HarnessSettingsStore
from gigaloom.types import (
    HarnessCapability,
    HarnessRequest,
    HarnessResult,
    availability_to_dict,
    parse_api_mode,
    parse_capability,
    result_to_dict,
    spec_capability_values,
    spec_to_dict,
)
from gigaloom.worktrees import parse_workspace_policy
from gigaloom.workspace import resolve_workspace
from gigaloom.workbench_execution import workbench_transport_projection
from gigaloom.workflows import (
    WorkflowCoordinator,
    WorkflowRepository,
    discover_workflows,
    load_workflow,
    parse_workflow_definition,
    workflow_definition_to_dict,
    workflow_plan,
    workflow_run_to_dict,
)

AGENT_ALIASES = {
    "codex": "codex-cli",
    "claude": "claude-code",
    "gemini": "gemini-cli",
}


def main(argv: list[str] | None = None) -> int:
    """Run the Unified Harness CLI."""
    from gigaloom.cli_commands.main import main as command_main

    return command_main(argv)


def _handle_doctor(args: argparse.Namespace, config: HarnessConfig) -> int:
    report = build_doctor_report(config, workspace=args.workspace)
    if args.output is not None:
        write_doctor_support_report(report, args.output)
    if args.json:
        _print_json(report)
    else:
        print(format_doctor_report(report))
    summary = report.get("summary") or {}
    blocked = int(summary.get("blocked") or 0)
    degraded = int(summary.get("degraded") or 0)
    if args.fail_on == "blocked" and blocked:
        return 1
    if args.fail_on == "degraded" and (blocked or degraded):
        return 1
    return 0


def _handle_bootstrap_preview(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = BootstrapService(config).preview(workspace=args.workspace)
    _print_bootstrap(payload, as_json=args.json)
    return 0


def _handle_bootstrap_apply(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = BootstrapService(config).apply(
        plan_id=args.plan_id,
        selected_steps=tuple(args.step),
        all_reversible=args.all_reversible,
        workspace=args.workspace,
    )
    _print_bootstrap(payload, as_json=args.json)
    return 0


def _handle_bootstrap_status(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = BootstrapService(config).status(args.application_id)
    _print_bootstrap(payload, as_json=args.json)
    return 0


def _handle_bootstrap_rollback(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = BootstrapService(config).rollback(
        args.application_id,
        workspace=args.workspace,
    )
    _print_bootstrap(payload, as_json=args.json)
    return 0


def _handle_compatibility_check(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    del config
    report = run_compatibility_guardian(
        create_default_registry(),
        harness_ids=tuple(args.harness) or None,
    )
    if args.json:
        _print_json(report)
    else:
        print(
            "Compatibility guardian: "
            f"{report['status']} ({report['summary']['passed']} passed, "
            f"{report['summary']['blocked']} blocked)"
        )
        for fixture in report["fixtures"]:
            print(
                f"- {fixture['id']}: {fixture['status']} "
                f"({fixture['category']}/{fixture['code']})"
            )
    return 0 if report["ok"] else 1


def _handle_handoff_capsule(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    store = FilesystemHarnessSessionStore(config.data_dir)
    service = HandoffCapsuleService(
        store=store,
        registry=create_default_registry(),
        runtime_store=RuntimeCoordinationStore(config.data_dir),
    )
    capsule = service.build(args.run_id, args.target_harness)
    if args.json:
        _print_json({"capsule": capsule})
    else:
        summary = capsule["summary"]
        provenance = capsule["provenance"]
        print(
            "Handoff capsule: "
            f"{capsule['capsule_id']} "
            f"({provenance['source']['harness_id']} -> "
            f"{provenance['target']['harness_id']})"
        )
        print(
            f"Artifacts: {summary['artifact_count']}; "
            f"pending approvals: {summary['pending_approval_count']}; "
            f"unresolved questions: {summary['unresolved_question_count']}"
        )
        print(
            "Continuity: evidence handoff only; native session identity is not moved."
        )
    return 0


def _handle_completion(args: argparse.Namespace, config: HarnessConfig) -> int:
    del config
    print(render_completion(args.shell), end="")
    return 0


def _handle_project_info(args: argparse.Namespace, config: HarnessConfig) -> int:
    payload = _project_payload(
        workspace=args.workspace,
        config=config,
        load_config_name=True,
    )
    if args.json:
        _print_json(payload)
    else:
        project = payload["project"]
        defaults = payload["defaults"]
        print(f"Project: {project['name']} ({project['id']})")
        print(f"Root: {project['root']}")
        if project["git_branch"]:
            print(f"Branch: {project['git_branch']}")
        print(f"Config: {payload['config']['path']}")
        print(f"Config exists: {payload['config']['exists']}")
        print(
            "Defaults: "
            f"{defaults['harness']} / {defaults['model']} / "
            f"{defaults['api_mode']} / {defaults['mode']}"
        )
    return 0


def _handle_project_init(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(
        args.workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    existed = project_config_path(project.root).exists()
    loaded = init_project_config(
        project.root,
        project_name=args.name,
        overwrite=args.overwrite,
    )
    payload = _project_payload(
        workspace=project.root,
        config=config,
        load_config_name=True,
    )
    if args.json:
        _print_json(payload)
    else:
        action = "Updated" if existed and args.overwrite else "Using existing"
        if not existed:
            action = "Initialized"
        print(f"{action} project config: {loaded.path}")
        print(f"Project: {payload['project']['name']} ({payload['project']['id']})")
    return 0


def _handle_harness_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    registry = create_default_registry()
    rows = []
    for harness in registry.list():
        spec = harness.spec()
        spec_payload = spec_to_dict(spec)
        availability = harness.availability()
        validation = registry.validation_report(spec.id) or validate_harness_spec(spec)
        rows.append(
            {
                "id": spec_payload["id"],
                "kind": spec_payload["kind"],
                "status": availability.status.value,
                "native": spec_payload["supports_native_sessions"],
                "default_invocation_mode": spec_payload["default_invocation_mode"],
                "workbench_transport": workbench_transport_projection(harness),
                "description": spec_payload["description"],
                "plugin_metadata": spec_payload["plugin_metadata"],
                "validation": harness_validation_report_to_dict(validation),
            }
        )
    if args.json:
        _print_json(rows)
    else:
        _print_table(rows)
    return 0


def _handle_harness_capabilities(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    from gigaloom.diagnostics.inventory.product import (
        build_product_inventory,
        canonical_inventory_json,
        load_product_inventory,
        validate_product_inventory,
    )

    registry = create_default_registry(include_entry_points=False)
    if args.agents and args.inventory:
        print(
            "product inventory: --agents and --inventory are mutually exclusive",
            file=sys.stderr,
        )
        return 2
    if args.inventory:
        from gigaloom.ui.app import create_app

        with tempfile.TemporaryDirectory(prefix="gigaloom-product-inventory-") as root:
            app = create_app(
                HarnessConfig(data_dir=root),
                registry=registry,
            )
            inventory = build_product_inventory(
                registry,
                cli_parser=build_parser(),
                api_routes=app.routes,
            )
        if args.check:
            errors = validate_product_inventory(
                inventory,
                repository_root=Path.cwd(),
            )
            if errors:
                for error in errors:
                    print(f"product inventory: {error}", file=sys.stderr)
                return 1
        payload = canonical_inventory_json(inventory)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        if args.json or (not args.check and args.output is None):
            print(payload, end="")
        return 0
    if args.check or args.output is not None:
        print(
            "product inventory: --check and --output require --inventory",
            file=sys.stderr,
        )
        return 2
    if args.agents:
        matrix = load_product_inventory()["agent_surface_capability_matrix"]
        renderer = render_agent_surface_capability_matrix_markdown
    else:
        matrix = build_adapter_capability_matrix(registry)
        renderer = render_adapter_capability_matrix_markdown
    if args.json:
        _print_json(matrix)
    else:
        print(renderer(matrix), end="")
    return 0


def _handle_config_path(args: argparse.Namespace, config: HarnessConfig) -> int:
    print(user_config_path())
    return 0


def _handle_config_set(args: argparse.Namespace, config: HarnessConfig) -> int:
    harness_id = _executable_config_harness_id(args.key)
    path = set_user_executable(
        harness_id,
        args.value,
        config_path=user_config_path(),
    )
    print(f"Updated {args.key} in {path}")
    return 0


def _handle_config_unset(args: argparse.Namespace, config: HarnessConfig) -> int:
    harness_id = _executable_config_harness_id(args.key)
    path, removed = unset_user_executable(
        harness_id,
        config_path=user_config_path(),
    )
    if removed:
        print(f"Removed {args.key} from {path}")
    else:
        print(f"No override configured for {args.key} in {path}")
    return 0


def _handle_integration_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    payload = IntegrationFlowService(config.data_dir).inventory()
    if args.json:
        _print_json(payload)
    else:
        print(
            "Integration sources: "
            + ", ".join(item["id"] for item in payload["sources"])
        )
        print(f"Targets: {len(payload['targets'])}")
        print(f"Catalog entries: {len(payload['catalog'])}")
        print(f"Recent flows: {len(payload['flows'])}")
    return 0


def _handle_integration_preview(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    try:
        configuration = json.loads(args.configuration_json)
    except json.JSONDecodeError as exc:
        raise ValueError("configuration-json must be valid JSON") from exc
    manifest = None
    if args.manifest:
        try:
            manifest = json.loads(
                Path(args.manifest).expanduser().read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("manifest must be a readable JSON object") from exc
    payload = IntegrationFlowService(config.data_dir).preview(
        {
            "source": args.source,
            "catalog_id": args.catalog_id,
            "manifest": manifest,
            "target_id": args.target,
            "scope": args.scope,
            "workspace": args.workspace,
            "package_id": args.package_id,
            "configuration": configuration,
        }
    )
    if args.json:
        _print_json(payload)
    else:
        plan = payload["plan"]
        print(f"Flow: {payload['flow']['id']}")
        print(f"Plan: {plan['plan_id']}")
        print(f"Package: {plan['package']['id']}@{plan['package']['version']}")
        print(f"Target: {plan['target']['id']} ({plan['target']['scope']})")
        print(f"Risk: {plan['risk']['decision']}")
        print(
            "Next: integration apply "
            f"{payload['flow']['id']} --plan-id {plan['plan_id']} "
            "--authority <operator>"
        )
    return 0


def _handle_integration_status(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = {
        "flow": integration_flow_record_to_dict(
            IntegrationFlowService(config.data_dir).get(args.flow_id)
        )
    }
    if args.json:
        _print_json(payload)
    else:
        flow = payload["flow"]
        print(f"Flow: {flow['id']}")
        print(f"Status: {flow['status']}")
        print(f"Verification: {flow['verification_status']}")
    return 0


def _handle_integration_apply(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = IntegrationFlowService(config.data_dir).apply(
        args.flow_id,
        plan_id=args.plan_id,
        authority=args.authority,
        allow_network=args.allow_network,
        allow_user_home=args.allow_user_home,
        native_consent_acknowledged=args.ack_native_consent,
    )
    if args.json:
        _print_json(payload)
    else:
        print(f"Flow: {payload['flow']['id']}")
        print(f"Status: {payload['flow']['status']}")
        print(f"Verification: {payload['flow']['verification_status']}")
    return 0


def _handle_integration_rollback(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = IntegrationFlowService(config.data_dir).rollback(args.flow_id)
    if args.json:
        _print_json(payload)
    else:
        print(f"Flow: {payload['flow']['id']}")
        print(f"Status: {payload['flow']['status']}")
    return 0


def _handle_integration_group_preview(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    try:
        configuration = json.loads(args.configuration_json)
    except json.JSONDecodeError as exc:
        raise ValueError("configuration-json must be valid JSON") from exc
    payload = GroupedIntegrationService(config.data_dir).preview(
        {
            "source": "catalog",
            "catalog_id": args.catalog_id,
            "scope": args.scope,
            "workspace": args.workspace,
            "configuration": configuration,
            "target_mode": "all_supported",
        }
    )
    if args.json:
        _print_json(payload)
    else:
        print(f"Group: {payload['group']['id']}")
        print(f"Plan: {payload['plan']['plan_id']}")
        print("Targets: " + ", ".join(payload["plan"]["target_ids"]))
        print(
            "Next: integration group-apply "
            f"{payload['group']['id']} --plan-id {payload['plan']['plan_id']} "
            "--authority <operator>"
        )
    return 0


def _handle_integration_pack_preview(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    try:
        mcp_configuration = json.loads(args.mcp_configuration_json)
    except json.JSONDecodeError as exc:
        raise ValueError("MCP configuration JSON is invalid") from exc
    payload = GroupedIntegrationService(config.data_dir).preview(
        {
            "component": "extension_pack",
            "pack_id": args.pack_id,
            "pack_version": args.pack_version,
            "skill_catalog_id": args.skill_catalog_id,
            "mcp_catalog_id": args.mcp_catalog_id,
            "scope": args.scope,
            "workspace": args.workspace,
            "target_mode": "all_supported",
            "mcp_configuration": mcp_configuration,
        }
    )
    if args.json:
        _print_json(payload)
    else:
        print(f"Pack: {payload['plan']['package']['id']}")
        print(f"Group: {payload['group']['id']}")
        print(f"Plan: {payload['plan']['plan_id']}")
        for item in payload["plan"]["compatibility"]:
            included = "included" if item["included"] else "excluded"
            print(f"Compatibility: {item['target']} {item['status']} ({included})")
        print(
            "Next: integration group-apply "
            f"{payload['group']['id']} --plan-id {payload['plan']['plan_id']} "
            "--authority <operator>"
        )
    return 0


def _handle_integration_group_status(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = {
        "group": integration_group_record_to_dict(
            GroupedIntegrationService(config.data_dir).get(args.group_id)
        )
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Group: {payload['group']['id']}")
        print(f"Status: {payload['group']['status']}")
    return 0


def _handle_integration_group_apply(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = GroupedIntegrationService(config.data_dir).apply(
        args.group_id,
        plan_id=args.plan_id,
        authority=args.authority,
        allow_network=args.allow_network,
        allow_user_home=args.allow_user_home,
        native_consent_acknowledged=args.ack_native_consent,
    )
    return _print_group_result(payload, json_output=args.json)


def _handle_integration_group_recover(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = GroupedIntegrationService(config.data_dir).recover(args.group_id)
    return _print_group_result(payload, json_output=args.json)


def _handle_integration_group_rollback(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    payload = GroupedIntegrationService(config.data_dir).rollback(args.group_id)
    return _print_group_result(payload, json_output=args.json)


def _print_group_result(payload: dict[str, Any], *, json_output: bool) -> int:
    if json_output:
        _print_json(payload)
    else:
        print(f"Group: {payload['group']['id']}")
        print(f"Status: {payload['group']['status']}")
        if payload["group"]["repair_actions"]:
            print("Repair: " + ", ".join(payload["group"]["repair_actions"]))
    return 0


def _handle_integration_scaffold(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    try:
        result = scaffold_integration_package(args.package_id, args.output)
    except FileExistsError as exc:
        raise ValueError(str(exc)) from exc
    print(f"Created integration scaffold: {result.root}")
    return 0


def _handle_integration_conformance(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    package = load_integration_package_document(args.manifest)
    descriptors = tuple(
        load_extension_target_document(path) for path in args.target_descriptor
    )
    report = run_integration_conformance(
        package,
        target_descriptors=descriptors,
    )
    payload = integration_conformance_report_to_dict(report)
    if args.json:
        _print_json(payload)
    else:
        print(
            f"Integration {report.package_id} {report.package_version}: "
            f"{'passed' if report.ok else 'failed'}"
        )
        for result in report.results:
            print(f"- {result.claim}: {result.status} ({result.detail})")
    return 0 if report.ok else 1


def _handle_harness_inspect(args: argparse.Namespace, config: HarnessConfig) -> int:
    registry = create_default_registry()
    harness = registry.get(args.harness_id)
    spec = harness.spec()
    validation = registry.validation_report(args.harness_id) or validate_harness_spec(
        spec
    )
    payload = {
        "spec": spec_to_dict(spec),
        "availability": availability_to_dict(harness.availability()),
        "workbench_transport": workbench_transport_projection(harness),
        "validation": harness_validation_report_to_dict(validation),
    }
    resolution = getattr(harness, "executable_resolution", None)
    if callable(resolution):
        payload.update(executable_resolution_to_dict(resolution()))
    capability_probe = getattr(harness, "capability_probe", None)
    if callable(capability_probe):
        payload["compatibility"] = cli_capability_snapshot_to_dict(capability_probe())
    if args.json:
        _print_json(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _handle_harness_validate(args: argparse.Namespace, config: HarnessConfig) -> int:
    registry = create_default_registry()
    harness = registry.get(args.harness_id)
    spec = harness.spec()
    report = registry.validation_report(args.harness_id) or validate_harness_spec(spec)
    payload = {
        "spec": spec_to_dict(spec),
        "validation": harness_validation_report_to_dict(report),
    }
    if args.json:
        _print_json(payload)
    else:
        status = "ok" if report.ok else "failed"
        print(f"Harness validation {status}: {args.harness_id}")
        for issue in report.issues:
            field = f" {issue.field}:" if issue.field else ""
            print(f"- {issue.level}{field} {issue.message}")
    return 0 if report.ok else 1


def _executable_config_harness_id(key: str) -> str:
    prefix = "executables."
    if not key.startswith(prefix) or len(key) == len(prefix):
        raise ValueError("Config key must use executables.<harness-id>")
    return key[len(prefix) :]


def _handle_harness_run(args: argparse.Namespace, config: HarnessConfig) -> int:
    result = _run_harness(
        harness_id=args.harness_id,
        prompt=args.prompt,
        model=args.model,
        api_mode=args.api_mode,
        capability=args.capability,
        mode=args.mode,
        workspace=args.workspace,
        dry_run=args.dry_run,
        native=args.native,
        config=config,
    )
    _print_result(result, as_json=args.json)
    return 0 if result.ok else 1


def _handle_chat(args: argparse.Namespace, config: HarnessConfig) -> int:
    result = _run_harness(
        harness_id="direct-chat",
        prompt=" ".join(args.prompt),
        model=args.model,
        api_mode=args.api_mode,
        capability=HarnessCapability.CHAT_COMPLETIONS.value,
        mode="plan",
        workspace=None,
        dry_run=args.dry_run,
        native=False,
        config=config,
    )
    _print_result(result, as_json=args.json)
    return 0 if result.ok else 1


def _handle_run_command(args: argparse.Namespace, config: HarnessConfig) -> int:
    action = args.prompt[0] if args.prompt else None
    if args.agent is None and action in {"patch", "pr-summary", "provenance", "replay"}:
        return _handle_run_artifact(args, config)
    if args.agent is None:
        print(
            "giga run requires --agent codex|claude|gemini or "
            "patch|pr-summary|provenance|replay <run_id>",
            file=sys.stderr,
        )
        return 2
    if not args.prompt:
        print("giga run --agent requires a prompt", file=sys.stderr)
        return 2
    return _handle_agent_alias(args, config)


def _handle_agent_alias(args: argparse.Namespace, config: HarnessConfig) -> int:
    harness_id = AGENT_ALIASES[args.agent]
    result = _run_harness(
        harness_id=harness_id,
        prompt=" ".join(args.prompt),
        model=args.model,
        api_mode=args.api_mode,
        capability=HarnessCapability.AGENT_CLI.value,
        mode=args.mode,
        workspace=args.workspace,
        dry_run=args.dry_run,
        native=args.native,
        config=config,
    )
    _print_result(result, as_json=args.json)
    return 0 if result.ok else 1


def _handle_run_artifact(args: argparse.Namespace, config: HarnessConfig) -> int:
    if len(args.prompt) != 2:
        print(f"Usage: giga run {args.prompt[0]} <run_id>", file=sys.stderr)
        return 2
    action, run_id = args.prompt
    store = FilesystemHarnessSessionStore(config.data_dir)
    run = store.get_run(run_id)
    if action == "replay":
        registry = create_default_registry()
        raw_request = _latest_raw_request_for_run(store, run)
        runtime = RuntimeCoordinationStore(config.data_dir)
        replay_payload = build_replay_request(
            run,
            raw_request=raw_request,
            reviewed_evidence=reviewed_evidence_manifest(
                run.id,
                runtime.list_policy_audit_events(run_id=run.id),
            ),
        )
        runner = HarnessSessionRunner(registry=registry, config=config, store=store)
        result = runner.run_in_session(run.session_id, replay_payload)
        if args.json:
            payload = result.to_dict()
            payload["source_run"] = run_to_dict(run)
            payload["replay_request"] = replay_payload
            _print_json(payload)
        else:
            _print_result(result.result, as_json=False)
        return 0 if result.result.ok else 1
    if action == "provenance":
        registry = create_default_registry()
        runtime = RuntimeCoordinationStore(config.data_dir)
        session = store.get_session(run.session_id)
        try:
            spec = registry.get(run.harness_id).spec()
        except UnknownHarnessError:
            spec = None
        provenance = build_run_provenance(
            run,
            session=session,
            spec=spec,
            raw_requests=store.list_raw_requests(run.session_id),
            raw_responses=store.list_raw_responses(run.session_id),
            events=store.list_events(run.session_id, run_id=run.id),
            policy_audit_events=runtime.list_policy_audit_events(run_id=run.id),
            data_dir=config.data_dir,
        )
        payload = {
            "run": run_to_dict(run),
            "provenance": run_provenance_to_dict(provenance),
        }
        if args.json:
            _print_json(payload)
        else:
            print(f"Run: {run.id}")
            print(f"Harness: {run.harness_id}")
            print(f"Status: {run.status.value}")
            print(f"Workspace: {run.workspace or '-'}")
            print(f"Replay: giga run replay {run.id}")
        return 0
    artifact = build_pr_artifact(run)
    artifact_payload = pr_artifact_to_dict(artifact)
    if action == "patch":
        if args.json:
            _print_json(
                {
                    "run": run_to_dict(run),
                    "patch": artifact.patch,
                    "pr_artifact": artifact_payload,
                }
            )
        else:
            print(artifact.patch)
        return 0
    if args.json:
        _print_json({"run": run_to_dict(run), "pr_artifact": artifact_payload})
    else:
        print(f"Title: {artifact.title}")
        print()
        print(artifact.body)
    return 0


def _handle_session_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    store = FilesystemHarnessSessionStore(config.data_dir)
    workspace = resolve_workspace(args.workspace) if args.workspace else None
    sessions = store.list_sessions(
        workspace=workspace,
        harness_id=args.harness_id,
        include_archived=args.include_archived,
    )
    rows = [_session_row(store, session.id) for session in sessions]
    if args.json:
        _print_json(rows)
    else:
        _print_session_table(rows)
    return 0


def _handle_session_show(args: argparse.Namespace, config: HarnessConfig) -> int:
    store = FilesystemHarnessSessionStore(config.data_dir)
    bundle = bundle_to_dict(store.get_session_bundle(args.session_id))
    if args.json:
        _print_json(bundle)
    else:
        session = bundle["session"]
        print(f"{session['title']} ({session['id']})")
        print(f"Harness: {session['default_harness_id']}")
        print(f"Updated: {session['updated_at']}")
        print(f"Messages: {len(bundle['messages'])}")
        print(f"Runs: {len(bundle['runs'])}")
    return 0


def _handle_session_create(args: argparse.Namespace, config: HarnessConfig) -> int:
    payload = _defined_values(
        title=args.title,
        workspace=args.workspace,
        harness_id=args.harness_id,
        model=args.model,
        api_mode=args.api_mode,
        mode=args.mode,
    )
    session = _session_application_service(config).create_session(
        payload,
        validate_harness=args.harness_id is not None,
    )
    serialized = session_to_dict(session)
    if args.json:
        _print_json({"session": serialized})
    else:
        print(f"Created session: {session.id}")
    return 0


def _handle_session_turn(args: argparse.Namespace, config: HarnessConfig) -> int:
    payload = _defined_values(
        prompt=args.prompt,
        harness_id=args.harness_id,
        model=args.model,
        api_mode=args.api_mode,
        capability=args.capability,
        mode=args.mode,
        workspace=args.workspace,
        permission_profile=args.permission_profile,
        execution_transport=args.transport,
    )
    service = _session_application_service(config)
    submission = service.submit_turn(
        args.session_id,
        payload,
        idempotency_key=args.idempotency_key or f"cli_{new_id('submit')}",
        origin="interactive",
    )
    response = {
        "session": session_to_dict(submission.queued.session),
        "run": run_to_dict(submission.queued.run),
        "job": job_to_dict(submission.job),
        "created": submission.created,
    }
    if args.json:
        _print_json(response)
    else:
        print(f"Submitted turn: {submission.queued.run.id}")
        print(f"Job: {submission.job.id} ({submission.job.status.value})")
    return 0


def _handle_session_events(args: argparse.Namespace, config: HarnessConfig) -> int:
    service = _session_application_service(config)
    run = service.get_run(args.run_id)
    events = service.list_run_events(args.run_id, after_id=args.after_id)
    payload = {
        "run": run_to_dict(run),
        "events": [event_to_dict(event) for event in events],
    }
    if args.json:
        _print_json(payload)
    else:
        for event in events:
            print(f"{event.created_at}  {event.type}  {event.message}")
    return 0


def _handle_session_approve(args: argparse.Namespace, config: HarnessConfig) -> int:
    service = _session_application_service(config)
    try:
        result = service.decide_approval(
            args.approval_id,
            args.decision,
            project_expiry_seconds=args.expires_in_seconds,
        )
    except KeyError as exc:
        raise ValueError(f"Unknown approval: {exc.args[0]}") from exc
    payload = {
        "approval": approval_request_to_dict(result.approval),
        "job_status": result.job.status.value if result.job else None,
        "retry_action": result.retry_action,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Approval {result.approval.id}: {result.approval.status.value}")
        if result.job is not None:
            print(f"Job: {result.job.id} ({result.job.status.value})")
    return 0


def _session_application_service(config: HarnessConfig) -> SessionApplicationService:
    registry = create_default_registry()
    store = FilesystemHarnessSessionStore(config.data_dir)
    runner = HarnessSessionRunner(registry=registry, config=config, store=store)
    runtime_store = RuntimeCoordinationStore(config.data_dir)
    dispatcher = DurableJobDispatcher(
        runtime_store=runtime_store,
        payload_store=DurableJobPayloadStore(config.data_dir),
        runner=runner,
    )
    return SessionApplicationService(
        runner=runner,
        settings_store=HarnessSettingsStore(config.data_dir, config),
        runtime_store=runtime_store,
        dispatcher=dispatcher,
    )


def _defined_values(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _handle_runtime_inspect(args: argparse.Namespace, config: HarnessConfig) -> int:
    store = RuntimeCoordinationStore(config.data_dir)
    payload = store.inspect()
    if args.json:
        _print_json(payload)
    else:
        print(f"Runtime database: {payload['path']}")
        print(f"Schema version: {payload['schema_version']}")
        print(f"Journal mode: {payload['journal_mode']}")
        print(f"Pending outbox: {payload['pending_outbox']}")
        for name, count in payload["counts"].items():
            print(f"{name}: {count}")
    return 0


def _handle_runtime_export(args: argparse.Namespace, config: HarnessConfig) -> int:
    store = RuntimeCoordinationStore(config.data_dir)
    payload = store.export()
    if args.output is None:
        _print_json(payload)
        return 0
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(output)
    print(f"Exported runtime coordination state to {output}")
    return 0


def _handle_benchmark_performance(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    del config
    report = run_performance_baseline(
        samples=args.samples,
        profile=args.profile,
    )
    if args.output:
        write_performance_report(args.output, report)
        print(f"Wrote private performance report to {Path(args.output).expanduser()}")
    else:
        _print_json(report)
    ci_blocking_metrics = report["baseline"]["ci_blocking_metrics"]
    return 1 if ci_blocking_metrics and report["status"] != "passed" else 0


def _handle_schedule_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = _schedule_project(args.workspace, config)
    rows = list(_schedule_service(config).list(project))
    if args.json:
        _print_json({"schedules": rows})
    else:
        for row in rows:
            state = row.get("state") or {}
            definition = row["definition"]
            print(
                f"{definition['id']}  {state.get('status', 'paused')}  "
                f"next={state.get('next_run_at') or '-'}  "
                f"target={definition['target']['kind']}:{definition['target']['id']}"
            )
    return 0


def _handle_schedule_show(args: argparse.Namespace, config: HarnessConfig) -> int:
    payload = _schedule_service(config).detail(
        _schedule_project(args.workspace, config), args.schedule_id
    )
    _print_json(payload)
    return 0


def _handle_schedule_write(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = _schedule_project(args.workspace, config)
    source = Path(args.definition).expanduser().read_text(encoding="utf-8")
    payload = yaml.safe_load(source)
    if not isinstance(payload, Mapping):
        raise ValueError("Schedule definition must be a YAML/JSON mapping")
    payload = {**payload, "workspace": project.root}
    if args.schedule_action == "preview":
        definition = build_schedule_definition(project, payload)
        result = {
            "definition": schedule_definition_to_dict(definition),
            "occurrences": list(next_occurrences(definition)),
            "dry_run": True,
        }
    else:
        result = _schedule_service(config).upsert(project, payload)
    _print_json(result)
    return 0


def _handle_schedule_action(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = _schedule_project(args.workspace, config)
    service = _schedule_service(config)
    method = args.schedule_action.replace("-", "_")
    if method == "delete":
        result = service.archive(project, args.schedule_id)
    elif method == "resume":
        result = service.enable(project, args.schedule_id)
    else:
        result = getattr(service, method)(project, args.schedule_id)
    _print_json(result)
    return 0


def _schedule_project(workspace: str | None, config: HarnessConfig):
    return resolve_project(
        workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )


def _schedule_service(config: HarnessConfig) -> ScheduleService:
    registry = create_default_registry()
    store = FilesystemHarnessSessionStore(config.data_dir)
    runner = HarnessSessionRunner(registry=registry, config=config, store=store)
    runtime_store = RuntimeCoordinationStore(config.data_dir)
    dispatcher = DurableJobDispatcher(
        runtime_store=runtime_store,
        payload_store=DurableJobPayloadStore(config.data_dir),
        runner=runner,
    )
    return ScheduleService(
        runtime_store=runtime_store,
        runner=runner,
        dispatcher=dispatcher,
        eval_store=FilesystemHarnessEvalStore(config.data_dir),
    )


def _handle_native_sync(args: argparse.Namespace, config: HarnessConfig) -> int:
    if not 1 <= args.limit <= 500:
        raise ValueError("native sync limit must be between 1 and 500")
    workspace = resolve_workspace(args.workspace) if args.workspace else None
    project_id = _project_id_for_workspace(workspace, config)
    registry = create_default_native_registry(data_dir=config.data_dir)
    index_store = FilesystemNativeSessionIndexStore(config.data_dir)
    result = registry.discover(
        harness_id=args.harness_id,
        workspace=workspace,
        include_external=args.include_external,
        cursor=args.cursor,
        limit=args.limit,
    )
    stored = [
        index_store.upsert_ref(
            ref,
            project_id=_native_ref_project_id(
                ref,
                workspace=workspace,
                project_id=project_id,
            ),
        )
        for ref in result.sessions
    ]
    payload = {
        "sessions": [native_session_ref_to_dict(ref) for ref in stored],
        "errors": [discovery_error_to_dict(error) for error in result.errors],
        "next_cursor": result.next_cursor,
        "scanned_count": result.scanned_count,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Synced {len(stored)} native session(s).")
        if result.next_cursor is not None:
            print(f"Next cursor: {result.next_cursor}")
        _print_native_table(payload["sessions"])
        for error in payload["errors"]:
            print(f"{error['harness_id']}: {error['message']}", file=sys.stderr)
    return 0 if not result.errors else 1


def _handle_native_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    workspace = resolve_workspace(args.workspace) if args.workspace else None
    project_id = _project_id_for_workspace(workspace, config)
    refs = FilesystemNativeSessionIndexStore(config.data_dir).list_refs(
        harness_id=args.harness_id,
        workspace=workspace,
        project_id=project_id,
        status=args.status,
        limit=args.limit,
    )
    if not args.include_external:
        refs = tuple(
            ref for ref in refs if ref.status is not NativeSessionStatus.EXTERNAL_NATIVE
        )
    rows = [native_session_ref_to_dict(ref) for ref in refs]
    if args.json:
        _print_json(rows)
    else:
        _print_native_table(rows)
    return 0


def _handle_native_import(args: argparse.Namespace, config: HarnessConfig) -> int:
    index_store = FilesystemNativeSessionIndexStore(config.data_dir)
    ref = index_store.get_ref(args.native_ref_id)
    if ref is None:
        raise ValueError(f"Native session not found: {args.native_ref_id}")
    connector = create_default_native_registry(data_dir=config.data_dir).get(
        ref.harness_id
    )
    imported = connector.import_ref(ref)
    session_store = FilesystemHarnessSessionStore(config.data_dir)
    snapshot = ref.execution_snapshot
    session = session_store.create_session(
        title=ref.title,
        workspace=ref.workspace,
        default_harness_id=ref.harness_id,
        default_model=(
            snapshot.model
            if snapshot is not None
            else _optional_text(ref.metadata.get("model"))
        ),
        default_api_mode=parse_api_mode(
            snapshot.api_mode if snapshot is not None else ref.metadata.get("api_mode")
        ),
        native={
            "source": "native_import",
            "native_ref_id": ref.id,
            "native_session_id": ref.native_session_id,
        },
        metadata=provider_native_title_metadata(
            _native_import_session_metadata(ref),
            provider=ref.harness_id,
            source_id=ref.native_session_id or ref.id,
        ),
    )
    messages = []
    skipped_count = 0
    for item in imported:
        role = _native_import_message_role(item.role)
        if role is None:
            skipped_count += 1
            session_store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=session.id,
                    run_id="native_import",
                    type="native_import_warning",
                    message="Skipped native transcript item with unknown role.",
                    payload={
                        "native_ref_id": ref.id,
                        "native_session_id": ref.native_session_id,
                        "role": item.role,
                        "metadata": _redacted_mapping(item.metadata),
                    },
                    created_at=item.created_at or utc_now(),
                )
            )
            continue
        messages.append(
            session_store.append_message(
                HarnessMessage(
                    id=new_id("msg"),
                    session_id=session.id,
                    run_id=None,
                    role=role,
                    content=str(redact_for_storage(item.content)),
                    created_at=item.created_at or utc_now(),
                    harness_id=ref.harness_id,
                    metadata={
                        "source": "native_import",
                        "native_ref_id": ref.id,
                        "native_session_id": ref.native_session_id,
                        **_redacted_mapping(item.metadata),
                    },
                )
            )
        )
    now = utc_now()
    link = session_store.append_native_link(
        session.id,
        HarnessNativeLink(
            id=new_id("nlink"),
            session_id=session.id,
            harness_id=ref.harness_id,
            status=NativeSessionStatus.IMPORTED,
            created_at=now,
            updated_at=now,
            native_session_id=ref.native_session_id,
            native_ref_id=ref.id,
            source=ref.source,
            workspace=ref.workspace,
            metadata={
                "source_status": ref.status.value,
                "imported_message_count": len(messages),
                "skipped_item_count": skipped_count,
                "project_id": ref.metadata.get("project_id"),
                **_native_snapshot_metadata(ref),
            },
        ),
    )
    payload = {
        "session": session_to_dict(session),
        "native_link": native_link_to_dict(link),
        "messages": [message_to_dict(message) for message in messages],
        "imported_message_count": len(messages),
        "skipped_item_count": skipped_count,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Imported native session into {session.id}")
        print(f"Messages: {len(messages)}")
        if skipped_count:
            print(f"Skipped: {skipped_count}")
    return 0


def _handle_preset_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    payload = _project_payload(
        workspace=args.workspace,
        config=config,
        load_config_name=True,
    )
    if args.json:
        _print_json(payload["presets"])
    else:
        _print_preset_table(payload["presets"])
    return 0


def _handle_preset_run(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(args.workspace, data_dir=config.data_dir)
    loaded = load_project_config(project.root)
    rendered = render_project_preset(
        project,
        loaded,
        args.preset_name,
        user_prompt=args.prompt,
        selected_files=tuple(args.selected_file or ()),
        last_run_diff=args.last_run_diff,
    )
    result = _run_harness(
        harness_id=rendered.harness or loaded.defaults.harness,
        prompt=rendered.prompt,
        model=args.model or rendered.model or loaded.defaults.model,
        api_mode=(
            args.api_mode
            or (rendered.api_mode.value if rendered.api_mode is not None else None)
            or loaded.defaults.api_mode.value
        ),
        capability=None,
        mode=args.mode or rendered.mode or loaded.defaults.mode,
        workspace=project.root,
        workspace_policy=rendered.workspace_policy,
        dry_run=args.dry_run,
        native=args.native
        or (
            rendered.invocation_mode is not None
            and rendered.invocation_mode is HarnessInvocationMode.NATIVE
        ),
        config=config,
    )
    if args.json:
        payload = rendered_project_preset_to_dict(rendered)
        payload["result"] = result_to_dict(result)
        _print_json(payload)
    else:
        _print_result(result, as_json=False)
    return 0 if result.ok else 1


def _handle_memory_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(
        args.workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    memories = FilesystemProjectMemoryStore().list(
        project,
        include_disabled=args.include_disabled,
    )
    rows = [memory_entry_to_dict(memory) for memory in memories]
    if args.json:
        _print_json({"project": project_to_dict(project), "memories": rows})
    else:
        _print_memory_table(rows)
    return 0


def _handle_memory_add(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(
        args.workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    memory = FilesystemProjectMemoryStore().add(
        project,
        text=" ".join(args.text),
        tags=tuple(args.tag or ()),
        source_session_id=args.session_id,
        source_run_id=args.run_id,
        enabled=not args.disabled,
    )
    payload = {
        "project": project_to_dict(project),
        "memory": memory_entry_to_dict(memory),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Added memory: {memory.id}")
    return 0


def _handle_memory_disable(args: argparse.Namespace, config: HarnessConfig) -> int:
    return _set_memory_enabled(args, config, enabled=False)


def _handle_memory_enable(args: argparse.Namespace, config: HarnessConfig) -> int:
    return _set_memory_enabled(args, config, enabled=True)


def _handle_memory_delete(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(
        args.workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    FilesystemProjectMemoryStore().delete(project, args.memory_id)
    payload = {"deleted": True, "project": project_to_dict(project)}
    if args.json:
        _print_json(payload)
    else:
        print(f"Deleted memory: {args.memory_id}")
    return 0


def _set_memory_enabled(
    args: argparse.Namespace,
    config: HarnessConfig,
    *,
    enabled: bool,
) -> int:
    project = resolve_project(
        args.workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    memory = FilesystemProjectMemoryStore().update(
        project,
        args.memory_id,
        enabled=enabled,
    )
    payload = {
        "project": project_to_dict(project),
        "memory": memory_entry_to_dict(memory),
    }
    if args.json:
        _print_json(payload)
    else:
        action = "Enabled" if enabled else "Disabled"
        print(f"{action} memory: {memory.id}")
    return 0


def _handle_eval_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(
        args.workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    specs, errors = discover_eval_specs(project.root)
    payload = {
        "project": project_to_dict(project),
        "specs": [eval_spec_to_dict(spec, include_cases=False) for spec in specs],
        "errors": [eval_spec_load_error_to_dict(error) for error in errors],
    }
    if args.json:
        _print_json(payload)
    else:
        _print_eval_spec_table(payload["specs"])
        for error in payload["errors"]:
            print(f"{error['path']}: {error['message']}", file=sys.stderr)
    return 0 if not errors else 1


def _handle_eval_run(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(
        args.workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    spec = load_eval_spec(project.root, args.eval_name)
    store = FilesystemHarnessSessionStore(config.data_dir)
    runner = HarnessSessionRunner(
        registry=create_default_registry(),
        config=config,
        store=store,
    )
    eval_run = run_eval(
        runner=runner,
        eval_store=FilesystemHarnessEvalStore(config.data_dir),
        project=project,
        spec=spec,
        harness_ids=_split_harness_args(args.harness),
        model=args.model,
        api_mode=args.api_mode,
        mode=args.mode,
        workspace_policy=args.workspace_policy,
        dry_run=args.dry_run,
    )
    payload = eval_run_to_dict(eval_run)
    if args.json:
        _print_json(payload)
    else:
        _print_eval_run_summary(payload)
    return 0 if eval_run.status == "passed" else 1


def _handle_agent_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(args.workspace, data_dir=config.data_dir)
    profiles, errors = discover_agent_profiles(project.root)
    payload = {
        "project": project_to_dict(project),
        "agents": [agent_profile_to_dict(profile) for profile in profiles],
        "errors": [error.__dict__ for error in errors],
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"{'ID':<22}{'Harness':<18}{'Mode':<8}Title")
        for profile in profiles:
            print(
                f"{profile.id:<22}{profile.harness_id:<18}{profile.mode:<8}{profile.title}"
            )
        for error in errors:
            print(f"{error.path}: {error.error}", file=sys.stderr)
    return 0 if not errors else 1


def _handle_agent_show(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(args.workspace, data_dir=config.data_dir)
    profile = load_agent_profile(project.root, args.agent_id)
    payload = agent_profile_to_dict(profile)
    if args.json:
        _print_json(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _handle_agent_validate(args: argparse.Namespace, config: HarnessConfig) -> int:
    path = Path(args.path).expanduser()
    profile = parse_agent_profile(
        path.read_text(encoding="utf-8"), source_path=str(path)
    )
    payload = {"valid": True, "profile": agent_profile_to_dict(profile)}
    if args.json:
        _print_json(payload)
    else:
        print(f"Agent profile valid: {profile.id}")
    return 0


def _handle_agent_profile_run(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(args.workspace, data_dir=config.data_dir)
    profile = load_agent_profile(project.root, args.agent_id)
    registry = create_default_registry()
    payload = agent_run_payload(
        profile,
        args.prompt,
        workspace=project.root,
        harness=registry.get(profile.harness_id),
        default_timeout_seconds=config.timeout_seconds,
    )
    payload["dry_run"] = args.dry_run
    runner = HarnessSessionRunner(
        registry=registry,
        config=config,
        store=FilesystemHarnessSessionStore(config.data_dir),
    )
    result = runner.create_and_run(payload)
    if args.json:
        _print_json(result.to_dict())
    else:
        _print_result(result.result, as_json=False)
    return 0 if result.result.ok else 1


def _handle_workflow_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(args.workspace, data_dir=config.data_dir)
    definitions, errors = discover_workflows(project.root)
    payload = {
        "workflows": [workflow_definition_to_dict(item) for item in definitions],
        "errors": [{"path": item.path, "error": item.error} for item in errors],
    }
    if args.json:
        _print_json(payload)
    else:
        _print_table(payload["workflows"])
        for error in payload["errors"]:
            print(f"Invalid {error['path']}: {error['error']}", file=sys.stderr)
    return 0 if not errors else 1


def _handle_workflow_show(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(args.workspace, data_dir=config.data_dir)
    definition = load_workflow(project.root, args.workflow_id)
    payload = {
        "workflow": workflow_definition_to_dict(definition),
        "plan": workflow_plan(definition),
    }
    if args.json:
        _print_json(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _handle_workflow_validate(args: argparse.Namespace, config: HarnessConfig) -> int:
    path = Path(args.path).expanduser()
    definition = parse_workflow_definition(
        path.read_text(encoding="utf-8"), source_path=str(path)
    )
    payload = {
        "valid": True,
        "workflow": workflow_definition_to_dict(definition),
        "plan": workflow_plan(definition),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Workflow valid: {definition.id} ({definition.source_hash})")
    return 0


def _handle_workflow_run(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(args.workspace, data_dir=config.data_dir)
    definition = load_workflow(project.root, args.workflow_id)
    inputs = _parse_workflow_inputs(args.input)
    if args.dry_run:
        payload = {
            "workflow": workflow_definition_to_dict(definition),
            "plan": workflow_plan(definition),
            "inputs": inputs,
        }
    else:
        coordinator = _workflow_coordinator(config, project)
        run = coordinator.start(definition, inputs=inputs, prompt=args.prompt)
        payload = {
            "run": workflow_run_to_dict(run, coordinator.repository.list_steps(run.id))
        }
    if args.json:
        _print_json(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _handle_workflow_status(args: argparse.Namespace, config: HarnessConfig) -> int:
    repository = WorkflowRepository(RuntimeCoordinationStore(config.data_dir))
    current = repository.get_run(args.run_id)
    project = resolve_project(current.project_root, data_dir=config.data_dir)
    coordinator = _workflow_coordinator(config, project)
    run = coordinator.advance(args.run_id)
    payload = workflow_run_to_dict(run, coordinator.repository.list_steps(run.id))
    if args.json:
        _print_json(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _handle_workflow_cancel(args: argparse.Namespace, config: HarnessConfig) -> int:
    repository = WorkflowRepository(RuntimeCoordinationStore(config.data_dir))
    current = repository.get_run(args.run_id)
    project = resolve_project(current.project_root, data_dir=config.data_dir)
    coordinator = _workflow_coordinator(config, project)
    run = coordinator.cancel(args.run_id)
    payload = workflow_run_to_dict(run, coordinator.repository.list_steps(run.id))
    if args.json:
        _print_json(payload)
    else:
        print(f"Canceled workflow run: {run.id}")
    return 0


def _workflow_coordinator(config: HarnessConfig, project: Any) -> WorkflowCoordinator:
    registry = create_default_registry()
    store = FilesystemHarnessSessionStore(config.data_dir)
    runner = HarnessSessionRunner(registry=registry, config=config, store=store)
    runtime_store = RuntimeCoordinationStore(config.data_dir)
    dispatcher = DurableJobDispatcher(
        runtime_store=runtime_store,
        payload_store=DurableJobPayloadStore(config.data_dir),
        runner=runner,
    )
    return WorkflowCoordinator(
        project=project,
        runtime_store=runtime_store,
        runner=runner,
        dispatcher=dispatcher,
    )


def _parse_workflow_inputs(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError("workflow --input values must use key=value")
        parsed[key.strip()] = value
    return parsed


def _handle_open_session(args: argparse.Namespace, config: HarnessConfig) -> int:
    store = FilesystemHarnessSessionStore(config.data_dir)
    session = store.get_session(args.session_id)
    workspace = (
        session.workspace or resolve_project(None, data_dir=config.data_dir).root
    )
    command = _editor_command_for_workspace(workspace, config)
    plan = build_open_workspace_plan(workspace, command=command)
    result = execute_editor_plan(plan, dry_run=args.dry_run)
    payload = {
        "session": session_to_dict(session),
        "editor": editor_open_plan_to_dict(result),
    }
    _print_editor_open(payload, as_json=args.json)
    return 0


def _handle_open_run(args: argparse.Namespace, config: HarnessConfig) -> int:
    store = FilesystemHarnessSessionStore(config.data_dir)
    run = store.get_run(args.run_id)
    workspace = workspace_for_run(run)
    if args.terminal:
        if workspace is None:
            raise ValueError("Run does not have a workspace to open in a terminal.")
        command = _terminal_command_for_workspace(workspace, config)
        plan = build_open_terminal_plan(workspace, command=command)
    elif args.diff:
        command = _editor_command_for_workspace(workspace, config)
        plan = build_open_diff_plan(run, data_dir=config.data_dir, command=command)
    else:
        command = _editor_command_for_workspace(workspace, config)
        plan = build_open_run_workspace_plan(run, command=command)
    result = execute_editor_plan(plan, dry_run=args.dry_run)
    payload = {
        "run": run_to_dict(run),
        "editor": editor_open_plan_to_dict(result),
    }
    _print_editor_open(payload, as_json=args.json)
    return 0


def _handle_open_file(args: argparse.Namespace, config: HarnessConfig) -> int:
    project = resolve_project(
        args.workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    loaded = load_project_config(project.root)
    plan = build_open_file_plan(
        project.root,
        args.path,
        command=loaded.editor.command,
        line=args.line,
        column=args.column,
    )
    result = execute_editor_plan(plan, dry_run=args.dry_run)
    payload = {
        "project": project_to_dict(project),
        "editor": editor_open_plan_to_dict(result),
    }
    _print_editor_open(payload, as_json=args.json)
    return 0


def _handle_harness_scaffold(args: argparse.Namespace, config: HarnessConfig) -> int:
    if args.output is None:
        print(render_adapter_module(args.harness_id), end="")
        return 0
    try:
        result = scaffold_adapter_package(args.harness_id, args.output)
    except FileExistsError as exc:
        raise ValueError(str(exc)) from exc
    print(f"Created adapter scaffold: {result.root}")
    print(f"Package: {result.package_name}")
    print(f"Files: {len(result.files)}")
    return 0


def _handle_harness_conformance(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    subject = load_installed_conformance_subject(args.harness_id)
    report = run_adapter_conformance(subject)
    payload = adapter_conformance_report_to_dict(report)
    if args.json:
        _print_json(payload)
    else:
        print(
            f"Adapter {report.adapter_id} {report.adapter_version}: "
            f"{'passed' if report.ok else 'failed'}"
        )
        for result in report.results:
            print(f"- {result.claim.value}: {result.status}")
    return 0 if report.ok else 1


def _run_harness(
    *,
    harness_id: str,
    prompt: str,
    model: str | None,
    api_mode: str | None,
    capability: str | None,
    mode: str,
    workspace: str | None,
    dry_run: bool,
    native: bool,
    config: HarnessConfig,
    workspace_policy: str | None = None,
):
    registry = create_default_registry()
    harness = registry.get(harness_id)
    spec = harness.spec()
    known_capabilities = spec_capability_values(spec)
    invocation_mode = (
        HarnessInvocationMode.NATIVE if native else HarnessInvocationMode.HEADLESS
    )
    execution_transport = (
        ExecutionTransport.NATIVE_TERMINAL if native else ExecutionTransport.ONE_SHOT
    )
    request = HarnessRequest(
        prompt=prompt,
        model=model,
        api_mode=parse_api_mode(api_mode or config.default_api_mode),
        capability=parse_capability(
            capability or (known_capabilities[0] if known_capabilities else None)
        ),
        mode=mode,
        invocation_mode=invocation_mode,
        execution_transport=execution_transport,
        workspace=resolve_workspace(workspace),
        extra=_run_extra(dry_run=dry_run, workspace_policy=workspace_policy),
    )
    preflight = build_preflight_report(
        prompt=request.prompt,
        workspace=request.workspace,
        data_dir=config.data_dir,
        permission_simulation=build_permission_simulation(
            spec=spec,
            execution_transport=execution_transport,
            invocation_mode=invocation_mode.value,
            permission_profile_id="interactive",
            mode=request.mode,
            workspace=request.workspace,
            api_mode=request.api_mode.value,
            model=request.model or config.default_model,
        ).to_dict(),
        readiness=build_execution_readiness(
            config,
            registry,
            harness_id=harness_id,
            invocation_mode=invocation_mode,
            execution_transport=execution_transport,
            api_mode=request.api_mode,
            model=request.model or config.default_model,
            mode=request.mode,
            workspace=request.workspace,
            workspace_policy=parse_workspace_policy(workspace_policy),
            durable=False,
            dry_run=dry_run,
        ),
    )
    if preflight.hard_block:
        return HarnessResult(
            ok=False,
            text="",
            raw={"preflight": preflight_report_to_dict(preflight)},
            error=format_preflight_block_message(preflight),
        )
    if native:
        if not spec.supports_native_sessions:
            return _result_with_preflight(
                HarnessResult(
                    ok=False,
                    text="",
                    error=f"Harness does not support native sessions: {harness_id}",
                ),
                preflight,
            )
        if not dry_run:
            return _result_with_preflight(
                HarnessResult(
                    ok=False,
                    text="",
                    error="Native CLI runs currently require --dry-run",
                ),
                preflight,
            )
        try:
            connector = create_default_native_registry(data_dir=config.data_dir).get(
                harness_id
            )
        except UnknownNativeHistoryConnectorError:
            return _result_with_preflight(
                HarnessResult(
                    ok=False,
                    text="",
                    error=f"Native connector is not registered: {harness_id}",
                ),
                preflight,
            )
        plan = connector.build_start_command(request, config.to_context())
        return _result_with_preflight(
            HarnessResult(
                ok=True,
                text="native dry run",
                raw={"native_command_plan": native_command_plan_to_dict(plan)},
                command=plan.command,
            ),
            preflight,
        )
    return _result_with_preflight(harness.run(request, config.to_context()), preflight)


def _print_result(result, *, as_json: bool) -> None:
    payload = result_to_dict(result)
    if as_json:
        _print_json(payload)
        return
    _print_readiness_remediation(payload)
    if result.ok:
        print(result.text)
    else:
        print(result.error or "harness failed", file=sys.stderr)


def _result_with_preflight(result: HarnessResult, preflight) -> HarnessResult:
    return replace(
        result,
        raw={
            **dict(result.raw),
            "preflight": preflight_report_to_dict(preflight),
        },
    )


def _print_readiness_remediation(payload: Mapping[str, Any]) -> None:
    raw = payload.get("raw")
    preflight = raw.get("preflight") if isinstance(raw, Mapping) else None
    readiness = preflight.get("readiness") if isinstance(preflight, Mapping) else None
    findings = readiness.get("findings") if isinstance(readiness, Mapping) else None
    for finding in findings or ():
        if not isinstance(finding, Mapping) or finding.get("status") == "ready":
            continue
        status = str(finding.get("status") or "degraded").upper()
        summary = str(finding.get("summary") or finding.get("id") or "readiness")
        print(f"Readiness [{status}]: {summary}", file=sys.stderr)
        for remedy in finding.get("remediation") or ():
            if not isinstance(remedy, Mapping):
                continue
            message = remedy.get("message")
            command = remedy.get("command")
            if message:
                print(f"  Remedy: {message}", file=sys.stderr)
            if command:
                print(f"  Command: {command}", file=sys.stderr)


def _print_bootstrap(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        _print_json(payload)
        return
    plan = payload.get("plan")
    if isinstance(plan, Mapping):
        print(f"Bootstrap plan: {plan.get('plan_id')}")
        for step in plan.get("steps") or ():
            if not isinstance(step, Mapping):
                continue
            status = "available" if step.get("available") else "not needed"
            print(f"- {step.get('id')}: {status}")
        print(f"Support export: {plan.get('support_export')}")
        return
    print(
        "Bootstrap application: "
        f"{payload.get('application_id')} ({payload.get('status')})"
    )
    if payload.get("rollback_available"):
        print(f"Rollback: giga bootstrap rollback {payload.get('application_id')}")


def _print_editor_open(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        _print_json(payload)
        return
    editor = payload["editor"]
    if editor.get("executed"):
        print(f"Opened {editor['target_path']}")
    else:
        print(editor["command_display"])


def _print_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'ID':<16}{'Kind':<14}{'Status':<12}{'Native':<8}Description")
    for row in rows:
        native = row.get("default_invocation_mode") if row.get("native") else "-"
        print(
            f"{row['id']:<16}{row['kind']:<14}{row['status']:<12}"
            f"{native:<8}{row['description']}"
        )


def _print_preset_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'Name':<18}{'Harness':<16}{'Mode':<8}{'Policy':<10}Title")
    for row in rows:
        print(
            f"{row['name']:<18}{(row.get('harness') or '-'):<16}"
            f"{(row.get('mode') or '-'):<8}"
            f"{(row.get('workspace_policy') or '-'):<10}{row['title']}"
        )


def _print_memory_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'ID':<38}{'Enabled':<10}{'Tags':<24}Text")
    for row in rows:
        tags = ",".join(row.get("tags") or ())
        preview = " ".join(str(row.get("text") or "").split())[:100]
        print(
            f"{row['id']:<38}{str(row.get('enabled', True)):<10}"
            f"{tags[:23]:<24}{preview}"
        )


def _print_eval_spec_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'Name':<20}{'Cases':<8}{'Harnesses':<28}Description")
    for row in rows:
        harnesses = ",".join(row.get("harnesses") or ()) or "echo"
        print(
            f"{row['name']:<20}{str(row.get('case_count') or 0):<8}"
            f"{harnesses[:27]:<28}{row.get('description') or ''}"
        )


def _print_eval_run_summary(payload: Mapping[str, Any]) -> None:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    print(f"Eval: {payload.get('spec_name')} ({payload.get('id')})")
    print(f"Status: {payload.get('status')}")
    print(
        "Score: "
        f"{summary.get('passed', 0)}/{summary.get('total', 0)} "
        f"passed, {summary.get('failed', 0)} failed, "
        f"{summary.get('errors', 0)} errors"
    )
    for result in payload.get("results") or ():
        print(
            f"- {result['case_id']} / {result['harness_id']}: "
            f"{result['status']} ({result['score']:.2f})"
        )


def _split_harness_args(values: list[str]) -> tuple[str, ...]:
    items: list[str] = []
    for value in values or ():
        items.extend(part.strip() for part in str(value).split(",") if part.strip())
    return tuple(dict.fromkeys(items))


def _run_extra(*, dry_run: bool, workspace_policy: str | None) -> dict[str, Any]:
    extra: dict[str, Any] = {"dry_run": dry_run}
    if workspace_policy is not None:
        extra["workspace_policy"] = workspace_policy
    return extra


def _session_row(
    store: FilesystemHarnessSessionStore,
    session_id: str,
) -> dict[str, Any]:
    session = store.get_session(session_id)
    messages = store.list_messages(session_id)
    runs = store.list_runs(session_id)
    row = session_to_dict(session)
    row.update(
        {
            "last_message_preview": (
                " ".join(messages[-1].content.split())[:120] if messages else ""
            ),
            "last_run_status": runs[-1].status if runs else None,
        }
    )
    return row


def _print_session_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'ID':<38}{'Updated':<22}{'Harness':<16}Title")
    for row in rows:
        print(
            f"{row['id']:<38}{row['updated_at']:<22}"
            f"{row['default_harness_id']:<16}{row['title']}"
        )


def _project_payload(
    *,
    workspace: str | None,
    config: HarnessConfig,
    load_config_name: bool,
) -> dict[str, Any]:
    project = resolve_project(
        workspace,
        data_dir=config.data_dir,
        load_config_name=load_config_name,
    )
    loaded = load_project_config(project.root)
    config_payload = project_config_to_dict(loaded)
    return {
        "project": project_to_dict(project),
        "config": config_payload,
        "defaults": config_payload["defaults"],
        "presets": list(config_payload["presets"].values()),
        "tools": list(config_payload["tools"].values()),
    }


def _project_id_for_workspace(
    workspace: str | None,
    config: HarnessConfig,
) -> str | None:
    if workspace is None:
        return None
    return resolve_project(
        workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    ).id


def _native_ref_project_id(
    ref: NativeSessionRef,
    *,
    workspace: str | None,
    project_id: str | None,
) -> str | None:
    if (
        project_id is not None
        and ref.workspace is not None
        and normalize_native_workspace(ref.workspace)
        == normalize_native_workspace(workspace)
    ):
        return project_id
    value = ref.metadata.get("project_id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _editor_command_for_workspace(
    workspace: str | None,
    config: HarnessConfig,
) -> str:
    project = resolve_project(
        workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    return load_project_config(project.root).editor.command


def _terminal_command_for_workspace(
    workspace: str | None,
    config: HarnessConfig,
) -> str:
    project = resolve_project(
        workspace,
        data_dir=config.data_dir,
        load_config_name=False,
    )
    return load_project_config(project.root).editor.terminal_command


def _latest_raw_request_for_run(
    store: FilesystemHarnessSessionStore,
    run,
):
    records = [
        record
        for record in store.list_raw_requests(run.session_id)
        if record.run_id == run.id
    ]
    return records[-1] if records else None


def _print_native_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'ID':<38}{'Harness':<16}{'Status':<18}{'Resume':<8}Title")
    for row in rows:
        resume = "yes" if row.get("can_resume") else "-"
        print(
            f"{row['id']:<38}{row['harness_id']:<16}"
            f"{row['status']:<18}{resume:<8}{row['title']}"
        )


def _native_import_session_metadata(ref: NativeSessionRef) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "native_import",
        "source_harness_id": ref.harness_id,
        "native_ref_id": ref.id,
        "native_session_id": ref.native_session_id,
        "native_status": ref.status.value,
    }
    project_id = _optional_text(ref.metadata.get("project_id"))
    if project_id is not None:
        metadata["project_id"] = project_id
    if ref.workspace is not None:
        metadata["project_root"] = ref.workspace
    metadata.update(_native_snapshot_metadata(ref))
    return metadata


def _native_snapshot_metadata(ref: NativeSessionRef) -> dict[str, Any]:
    if ref.execution_snapshot is None:
        return {"limitations": ["route_unknown"]} if ref.can_resume else {}
    return {
        "execution_snapshot": execution_snapshot_to_dict(ref.execution_snapshot),
        "limitations": (
            [] if ref.execution_snapshot.route_known else ["route_unknown"]
        ),
    }


def _native_import_message_role(role: str) -> str | None:
    normalized = str(role).strip().lower()
    if normalized in {"user", "assistant", "system", "tool"}:
        return normalized
    if normalized == "model":
        return "assistant"
    return None


def _redacted_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        value = {}
    redacted = redact_for_storage(dict(value))
    return dict(redacted) if isinstance(redacted, dict) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


if __name__ == "__main__":
    raise SystemExit(main())
