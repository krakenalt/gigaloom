"""Failing-first ownership and integration boundaries for GigaLoom 0.9."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import tomllib
from typing import Any

import pytest

from gigaloom.execution.route_advisor.models import RouteRejectionCode


BOUNDARY_MANIFEST = "architecture/gigaloom-0.9-boundaries.json"


@pytest.fixture(scope="module")
def boundaries(repository_root: Path) -> dict[str, Any]:
    return json.loads((repository_root / BOUNDARY_MANIFEST).read_text(encoding="utf-8"))


def test_existing_owners_are_reused_without_parallel_stores(
    repository_root: Path,
    boundaries: dict[str, Any],
) -> None:
    owners = boundaries["owners"]
    assert set(owners) == {
        "session_state",
        "route_admission",
        "route_receipts",
        "process_leases",
        "launch_plans",
        "evidence_projections",
        "editor_schemas",
    }
    for name, relative in owners.items():
        path = repository_root / relative
        if name != "editor_schemas":
            assert path.is_dir(), f"missing frozen {name} owner: {relative}"
    assert all(
        not (repository_root / relative).exists()
        for relative in boundaries["forbidden_parallel_owner_roots"]
    )


def test_no_new_gpt2giga_protocol_or_provider_private_imports(
    repository_root: Path,
    boundaries: dict[str, Any],
) -> None:
    actual = _gpt2giga_private_references(repository_root / "src/gigaloom")
    expected = {
        (item["path"], item["module"])
        for item in boundaries["gateway"]["legacy_private_import_exceptions"]
    }
    assert actual == expected


def test_gateway_artifact_version_and_digests_are_bound(
    repository_root: Path,
    boundaries: dict[str, Any],
) -> None:
    gateway = boundaries["gateway"]
    handoff = json.loads(
        (repository_root / gateway["artifact_handoff"]).read_text(encoding="utf-8")
    )
    metadata = tomllib.loads(
        (repository_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    lock = tomllib.loads((repository_root / "uv.lock").read_text(encoding="utf-8"))
    locked = [item for item in lock["package"] if item["name"] == "gpt2giga"]

    assert gateway["allowed_boundaries"] == ["console_script", "http"]
    assert metadata["project"]["optional-dependencies"]["gpt2giga"] == [
        gateway["requirement"]
    ]
    assert len(locked) == 1
    assert locked[0]["version"] == gateway["version"] == handoff["artifact"]["version"]
    assert locked[0]["source"] == {"registry": "https://pypi.org/simple"}
    assert locked[0]["sdist"]["hash"] == (
        f"sha256:{handoff['artifact']['sdist_sha256']}"
    )
    assert {item["hash"] for item in locked[0]["wheels"]} == {
        f"sha256:{handoff['artifact']['wheel_sha256']}"
    }


def test_gateway_contracts_store_references_not_plaintext_credentials(
    repository_root: Path,
    boundaries: dict[str, Any],
) -> None:
    gateway = boundaries["gateway"]
    fields = set(gateway["persisted_secret_fields"])
    forbidden = set(gateway["forbidden_plaintext_secret_fields"])
    assert fields == {"auth_ref", "tls_policy_ref", "redacted_env_delta"}
    assert fields.isdisjoint(forbidden)
    assert all(name.endswith("_ref") or name.startswith("redacted_") for name in fields)
    handoff = json.loads(
        (repository_root / gateway["artifact_handoff"]).read_text(encoding="utf-8")
    )
    assert _mapping_keys(handoff).isdisjoint(forbidden)

    plaintext_assignments: list[str] = []
    for relative in (
        "src/gigaloom/providers/gateway",
        "src/gigaloom/native/launch",
        "src/gigaloom/execution/route_advisor",
    ):
        for path in (repository_root / relative).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if _plaintext_secret_assignment(node, forbidden):
                    plaintext_assignments.append(
                        f"{path.relative_to(repository_root)}:{node.lineno}"
                    )
    assert plaintext_assignments == []


def test_gateway_and_launch_planning_do_not_write_native_homes(
    repository_root: Path,
    boundaries: dict[str, Any],
) -> None:
    roots = (
        repository_root / "src/gigaloom/providers/gateway",
        repository_root / "src/gigaloom/native/launch",
    )
    violations: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            has_home_resolution = any(
                _is_path_home_call(node) for node in ast.walk(tree)
            )
            native_literals = {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in boundaries["gateway"]["native_homes"]
            }
            has_write = any(_is_path_write_call(node) for node in ast.walk(tree))
            if has_write and (has_home_resolution or native_literals):
                violations.append(path.relative_to(repository_root).as_posix())
    assert violations == []
    assert boundaries["gateway"]["managed_write_roots"] == [
        "GIGALOOM_DATA_DIR",
        "bounded_temp",
    ]


def test_editor_schema_assets_have_one_owner(
    repository_root: Path,
    boundaries: dict[str, Any],
) -> None:
    policy = boundaries["editor_schema_assets"]
    owner = repository_root / policy["owner"]
    matching = [
        path
        for filename in policy["filenames"]
        for path in repository_root.rglob(filename)
        if ".venv" not in path.parts and "node_modules" not in path.parts
    ]
    assert all(path.is_relative_to(owner) for path in matching)
    assert policy["owner"] == boundaries["owners"]["editor_schemas"]


def test_web_feature_and_request_graph_remain_bounded(
    repository_root: Path,
    boundaries: dict[str, Any],
) -> None:
    web = boundaries["web"]
    assert web["request_graph_owners"] == [
        "web/src/request-graph.ts",
        "web/src/remaining-request-graph.ts",
    ]
    for relative, limit in web["maximum_lines"].items():
        assert _line_count(repository_root / relative) <= limit, relative
    feature_root = repository_root / "web/src/features"
    oversized = [
        path.relative_to(repository_root).as_posix()
        for path in feature_root.rglob("*")
        if path.suffix in {".ts", ".tsx"}
        and _line_count(path) > web["feature_module_maximum_lines"]
    ]
    assert oversized == []


def test_thread_relay_contracts_freeze_authority_and_delivery_bounds(
    boundaries: dict[str, Any],
) -> None:
    relay = boundaries["thread_relay"]
    assert relay["contract_owner"].startswith(boundaries["owners"]["session_state"])
    assert set(relay["contracts"]) == {
        "ThreadLocatorV1",
        "ThreadReadProjectionV1",
        "ThreadMessageEnvelopeV1",
        "ThreadDeliveryReceiptV1",
    }
    envelope = set(relay["contracts"]["ThreadMessageEnvelopeV1"])
    assert {
        "actor_binding",
        "project_binding",
        "idempotency_key",
        "expires_at",
        "depth",
        "expected_target_revision",
        "expected_active_turn_id",
    } <= envelope
    assert relay["role"] == "user"
    assert relay["author_modes"] == [
        "user_authored",
        "agent_proposed_user_approved",
    ]
    assert relay["actor_binding_required"] is True
    assert relay["project_binding_required"] is True
    assert relay["ttl_required"] is True
    assert relay["idempotency_required"] is True
    assert relay["cross_project_default"] == "deny"
    assert relay["max_depth"] == 1
    assert relay["max_outstanding_children"] == 4
    assert relay["mutations_require_target_revision"] is True
    assert relay["steer_requires_active_turn_id"] is True
    assert relay["receipt_content"] == "digest_only"


def test_unknown_and_stale_capability_revisions_fail_closed(
    boundaries: dict[str, Any],
) -> None:
    drift = boundaries["capability_drift"]
    assert drift == {
        "unknown": "deny",
        "stale": "deny",
        "fallback_route": "forbidden",
        "revalidation": "explicit",
    }
    reasons = {item.value for item in RouteRejectionCode}
    assert {
        "capability_snapshot_unknown",
        "capability_snapshot_stale",
    } <= reasons


def test_support_truth_does_not_promote_the_released_gateway(
    repository_root: Path,
    boundaries: dict[str, Any],
) -> None:
    gateway = boundaries["gateway"]
    handoff = json.loads(
        (repository_root / gateway["artifact_handoff"]).read_text(encoding="utf-8")
    )
    assert gateway["support_statuses"] == [
        "stable",
        "technical_preview",
        "vendor_unsupported",
        "blocked",
    ]
    assert handoff["codex_responses_gigachat"]["support_status"] == (
        "technical_preview"
    )


def _gpt2giga_private_references(package_root: Path) -> set[tuple[str, str]]:
    references: set[tuple[str, str]] = set()
    repository_root = package_root.parents[1]
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(repository_root).as_posix()
        for node in ast.walk(tree):
            candidates: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                candidates = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                candidates = (node.module,)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                candidates = (node.value,)
            for module in candidates:
                if module == "gpt2giga.cli" or module.startswith("gpt2giga.providers."):
                    references.add((relative, module))
    return references


def _is_path_home_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "Path"
        and node.func.attr == "home"
    )


def _is_path_write_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        in {"write_text", "write_bytes", "mkdir", "touch", "replace", "rename"}
    )


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _mapping_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in _mapping_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _mapping_keys(child)}
    return set()


def _plaintext_secret_assignment(node: ast.AST, forbidden: set[str]) -> bool:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    else:
        return False
    return (
        isinstance(value, ast.Constant)
        and isinstance(value.value, str)
        and bool(value.value)
        and any(
            isinstance(target, ast.Name) and target.id in forbidden
            for target in targets
        )
    )
