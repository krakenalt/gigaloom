"""Review service primitives."""

from __future__ import annotations

from typing import Any, Mapping

from gigaloom.projects.api import resolve_project
from gigaloom.review.ports import (
    DurableJobDispatcher,
    ExecutionTransport,
    HarnessRun,
    HarnessSessionStore,
    HeadlessManagedMCPSnapshotStore,
    title_from_prompt,
    utc_now,
)
from gigaloom.session_runner import HarnessSessionRunner

from .codec import _mapping, _required_hash, _required_target, _trace_replay_axis
from .dimensions import _extension_source_reference, extension_target_reference
from .evidence import _latest_raw_request, _run_messages
from .manifest import manifest_from_dict
from .models import TraceReplayAxis, TraceReplayConflictError, TraceReplayManifest
from .preparation import prepare_trace_replay
from .projection import trace_replay_projection


class TraceReplayService:
    """Preview, execute, and compare one-axis replays through existing owners."""

    def __init__(
        self,
        runner: HarnessSessionRunner,
        *,
        dispatcher: DurableJobDispatcher | None = None,
    ) -> None:
        self.runner = runner
        self.store: HarnessSessionStore = runner.store
        self.dispatcher = dispatcher
        self.snapshot_store = HeadlessManagedMCPSnapshotStore(runner.config.data_dir)

    def preview(
        self,
        source_run_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build a stale-safe preview without creating a session or worktree."""
        source_run = self.store.get_run(source_run_id)
        raw_request = _latest_raw_request(self.store, source_run)
        source_messages = _run_messages(self.store, source_run)
        source_events = self.store.list_events(
            source_run.session_id, run_id=source_run.id
        )
        axis = _trace_replay_axis(payload.get("axis"))
        target = _required_target(payload.get("target"))
        target_extension = self._target_extension(
            source_run,
            axis=axis,
            target=target,
        )
        manifest, replay_payload = prepare_trace_replay(
            source_run,
            raw_request=raw_request,
            payload=payload,
            source_messages=source_messages,
            source_events=source_events,
            target_extension=target_extension,
            created_at=utc_now(),
        )
        expected = str(payload.get("manifest_sha256") or "").strip()
        if expected and expected != manifest.manifest_sha256:
            raise TraceReplayConflictError("trace replay source evidence changed")
        admission = self._admission(
            manifest,
            replay_payload=replay_payload,
        )
        return {
            "manifest": manifest.to_dict(),
            "admission": admission,
            "execution": {
                "new_session": True,
                "workspace_policy": replay_payload.get("workspace_policy"),
                "provider_session": "new",
                "external_telemetry_required": False,
                "automatic_apply": False,
            },
        }

    def start(
        self,
        source_run_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Start one exact reviewed replay through the existing runner/dispatcher."""
        expected = _required_hash(payload.get("manifest_sha256"), "manifest_sha256")
        preview = self.preview(source_run_id, payload)
        manifest = manifest_from_dict(_mapping(preview["manifest"]))
        if expected != manifest.manifest_sha256:
            raise TraceReplayConflictError("trace replay preview is stale")
        admission = _mapping(preview["admission"])
        if not bool(admission.get("admitted")):
            raise ValueError(
                str(admission.get("reason_code") or "trace_replay_not_admitted")
            )
        source_run = self.store.get_run(source_run_id)
        raw_request = _latest_raw_request(self.store, source_run)
        axis = manifest.axis
        target_extension = self._target_extension(
            source_run,
            axis=axis,
            target=_required_target(payload.get("target")),
        )
        _, replay_payload = prepare_trace_replay(
            source_run,
            raw_request=raw_request,
            payload=payload,
            source_messages=_run_messages(self.store, source_run),
            source_events=self.store.list_events(
                source_run.session_id, run_id=source_run.id
            ),
            target_extension=target_extension,
            created_at=manifest.created_at,
        )
        replay_session = self.runner.create_session(
            title=f"Trace replay: {title_from_prompt(source_run.prompt)}",
            workspace=source_run.workspace,
            default_harness_id=str(replay_payload["harness_id"]),
            default_model=(
                str(replay_payload["model"])
                if replay_payload.get("model") is not None
                else None
            ),
            default_api_mode=source_run.api_mode,
            default_mode=source_run.mode,
        )
        replay_session = self.store.update_session(
            replay_session.id,
            metadata={
                **dict(replay_session.metadata),
                "shared_attachment_session_id": source_run.session_id,
                "trace_replay": {
                    "manifest": manifest.to_dict(),
                    "source_run_id": source_run.id,
                    "source_session_id": source_run.session_id,
                    "destination_run_id": None,
                },
            },
        )
        if (
            replay_payload.get("execution_transport")
            == ExecutionTransport.NATIVE_STRUCTURED.value
        ):
            if self.dispatcher is None:
                raise ValueError(
                    "native_structured trace replay requires the durable runtime"
                )
            submission = self.dispatcher.submit(
                replay_session.id,
                replay_payload,
                idempotency_key=(
                    f"trace-replay:{manifest.manifest_sha256}:{replay_session.id}"
                ),
                origin="manual",
            )
            destination_run = submission.queued.run
        else:
            destination_run = self.runner.run_in_session(
                replay_session.id,
                replay_payload,
            ).run
        self.store.update_session(
            replay_session.id,
            metadata={
                **dict(replay_session.metadata),
                "trace_replay": {
                    "manifest": manifest.to_dict(),
                    "source_run_id": source_run.id,
                    "source_session_id": source_run.session_id,
                    "destination_run_id": destination_run.id,
                },
            },
        )
        return self.projection(destination_run.id)

    def projection(self, destination_run_id: str) -> dict[str, Any]:
        """Read one retained replay and compute its bounded comparison."""
        destination_run = self.store.get_run(destination_run_id)
        destination_session = self.store.get_session(destination_run.session_id)
        retained = _mapping(destination_session.metadata.get("trace_replay"))
        manifest = manifest_from_dict(_mapping(retained.get("manifest")))
        if retained.get("destination_run_id") not in {None, destination_run.id}:
            raise TraceReplayConflictError("trace replay destination identity changed")
        source_run = self.store.get_run(manifest.source_run_id)
        return trace_replay_projection(
            manifest,
            source_run=source_run,
            destination_run=destination_run,
            source_raw_request=_latest_raw_request(self.store, source_run),
            destination_raw_request=_latest_raw_request(self.store, destination_run),
            source_messages=_run_messages(self.store, source_run),
            destination_messages=_run_messages(self.store, destination_run),
            source_events=self.store.list_events(
                source_run.session_id, run_id=source_run.id
            ),
            destination_events=self.store.list_events(
                destination_run.session_id, run_id=destination_run.id
            ),
        )

    def _target_extension(
        self,
        source_run: HarnessRun,
        *,
        axis: TraceReplayAxis,
        target: str,
    ) -> Mapping[str, Any] | None:
        if axis is not TraceReplayAxis.EXTENSIONS:
            return None
        reference = extension_target_reference(target)
        if reference is None:
            return None
        if source_run.workspace is None:
            raise ValueError("extension replay requires a project workspace")
        raw_request = _latest_raw_request(self.store, source_run)
        source_extension = _extension_source_reference(
            source_run,
            _mapping(raw_request.payload if raw_request is not None else None),
        )
        if source_extension:
            reference["project_id"] = str(source_extension.get("project_id") or "")
            reference["harness_id"] = source_run.harness_id
        snapshot = self.snapshot_store.load(reference)
        source_project = resolve_project(
            source_run.workspace,
            data_dir=self.runner.config.data_dir,
            load_config_name=False,
        )
        if snapshot.project_id != source_project.id:
            raise ValueError("extension replay target belongs to another project")
        if snapshot.harness_id != source_run.harness_id:
            raise ValueError("extension replay target belongs to another harness")
        return snapshot.public_ref()

    def _admission(
        self,
        manifest: TraceReplayManifest,
        *,
        replay_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            self.runner.registry.get(str(replay_payload["harness_id"]))
        except KeyError:
            return {
                "admitted": False,
                "reason_code": "unknown_target_harness",
            }
        if manifest.axis is TraceReplayAxis.PROVIDER:
            return {
                "admitted": False,
                "reason_code": "provider_axis_requires_route_authority",
            }
        source_extensions = manifest.source_dimensions["extensions"]
        if manifest.axis is TraceReplayAxis.HARNESS and source_extensions is not None:
            return {
                "admitted": False,
                "reason_code": "harness_axis_extension_snapshot_incompatible",
            }
        return {"admitted": True, "reason_code": None}
