from __future__ import annotations

import json
from pathlib import Path

from gigaloom.config import HarnessConfig
from gigaloom.harnesses.base import BaseHarness
from gigaloom.registry import HarnessRegistry
from gigaloom.review.capsules import (
    FilesystemRunCapsuleRepository,
    RunCapsuleCapturePortsV1,
    RunCapsuleLifecycleService,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    verify_run_capsule,
)
from gigaloom.session_runner import HarnessSessionRunner
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.types import (
    Availability,
    HarnessContext,
    HarnessCapability,
    HarnessRequest,
    HarnessResult,
    HarnessSpec,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "run_capsules" / "read_only_run.json"


class _Harness(BaseHarness):
    @classmethod
    def spec(cls) -> HarnessSpec:
        return HarnessSpec(
            id="capsule-fixture",
            title="Capsule fixture",
            kind="test",
            description="Capture a deterministic capsule fixture",
            capabilities=(HarnessCapability.CHAT_COMPLETIONS,),
        )

    def availability(self) -> Availability:
        return Availability.available("test")

    def run(self, request: HarnessRequest, context: HarnessContext) -> HarnessResult:
        del context
        return HarnessResult(ok=True, text=f"answer:{request.prompt}")


class _Ports:
    def __init__(self) -> None:
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def input_lock_for_run(self, run_id: str):
        del run_id
        return build_input_lock(self.payload["input_lock"])

    def output_receipt_for_run(self, run_id: str):
        payload = {**self.payload["output_receipt"]}
        payload["run"] = {**payload["run"], "run_id": run_id}
        return build_output_receipt(payload)

    def artifact_manifest_for_run(self, run_id: str):
        del run_id
        return build_artifact_manifest(self.payload["artifacts"])

    def omission_manifest_for_run(self, run_id: str):
        del run_id
        return build_omission_manifest(self.payload["omissions"])


def test_terminal_run_captures_one_content_free_capsule_idempotently(
    tmp_path: Path,
) -> None:
    registry = HarnessRegistry()
    registry.register(_Harness())
    repository = FilesystemRunCapsuleRepository(tmp_path / "state")
    ports = _Ports()
    lifecycle = RunCapsuleLifecycleService(
        ports=RunCapsuleCapturePortsV1(ports, ports, ports),
        repository=repository,
    )
    runner = HarnessSessionRunner(
        registry=registry,
        config=HarnessConfig(data_dir=str(tmp_path / "state")),
        store=InMemoryHarnessSessionStore(),
        run_completion_hook=lifecycle,
    )

    result = runner.create_and_run(
        {"harness_id": "capsule-fixture", "prompt": "do not retain me"}
    )

    [binding] = result.run.metadata["completion_artifacts"]
    record = repository.get_by_run(result.run.id)
    assert binding == {
        "schema_version": 1,
        "kind": "run_capsule",
        "artifact_id": record.capsule_id,
        "sha256": record.capsule_sha256,
        "status": "captured",
        "attributes": {
            "archive_sha256": record.archive_sha256,
            "content_free": True,
            "signature_status": "unsigned",
            "signer_id": None,
            "trust_status": None,
        },
    }
    archive = repository.archive_for_run(result.run.id)
    assert verify_run_capsule(archive).verified is True
    assert b"do not retain me" not in archive.read_bytes()
    assert (
        lifecycle.on_run_completed(
            result.run.id,
            completed_at=record.created_at,
        )[0].artifact_id
        == record.capsule_id
    )
