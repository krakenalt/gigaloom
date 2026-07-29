# ruff: noqa: E402, F401, F403, F405
"""Internal integration flow implementation slice."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .models import *  # noqa: F403
from .projection import *  # noqa: F403
from .resolution import *  # noqa: F403
from .state import *  # noqa: F403


class _FlowPersistenceMixin:
    """Implementation slice for application-owned integration flows."""

    def _transition(
        self,
        record: IntegrationFlowRecord,
        status: IntegrationFlowStatus,
        stage: str,
        *,
        receipt_id: str | None = None,
        verification_status: str | None = None,
        rollback_available: bool | None = None,
        error_code: str | None = None,
    ) -> IntegrationFlowRecord:
        timestamp = self._timestamp()
        updated = replace(
            record,
            status=status,
            receipt_id=receipt_id if receipt_id is not None else record.receipt_id,
            verification_status=(
                verification_status
                if verification_status is not None
                else record.verification_status
            ),
            rollback_available=(
                rollback_available
                if rollback_available is not None
                else record.rollback_available
            ),
            error_code=error_code,
            updated_at=timestamp,
            events=(
                *record.events[-(MAX_FLOW_EVENTS - 1) :],
                IntegrationFlowEvent(
                    stage=stage,
                    status=status.value,
                    occurred_at=timestamp,
                    code=error_code,
                ),
            ),
        )
        self._put(updated)
        return updated

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _ensure_catalog_seeded(self) -> None:
        import_builtin_skills(self.catalog)

    def _put(self, record: IntegrationFlowRecord) -> None:
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            records = self._read_records_unlocked()
            records[record.id] = record
            if len(records) > MAX_INTEGRATION_FLOWS:
                oldest = sorted(records.values(), key=lambda item: item.updated_at)
                records = {item.id: item for item in oldest[-MAX_INTEGRATION_FLOWS:]}
            self._write_records_unlocked(records)

    def _read_records(self) -> dict[str, IntegrationFlowRecord]:
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            return self._read_records_unlocked()

    def _read_records_unlocked(self) -> dict[str, IntegrationFlowRecord]:
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or not self.path.is_file():
            raise IntegrationFlowError("integration flow state is unsafe")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IntegrationFlowError("integration flow state is unreadable") from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            raise IntegrationFlowError("integration flow state schema is unsupported")
        raw_records = payload.get("flows")
        if (
            not isinstance(raw_records, list)
            or len(raw_records) > MAX_INTEGRATION_FLOWS
        ):
            raise IntegrationFlowError("integration flow state is invalid")
        records = tuple(_record_from_dict(item) for item in raw_records)
        if len({item.id for item in records}) != len(records):
            raise IntegrationFlowError("integration flow state has duplicate ids")
        return {item.id: item for item in records}

    def _write_records_unlocked(
        self, records: Mapping[str, IntegrationFlowRecord]
    ) -> None:
        payload = {
            "schema_version": INTEGRATION_FLOW_SCHEMA_VERSION,
            "flows": [_private_record_to_dict(records[key]) for key in sorted(records)],
        }
        self._atomic_private_json(payload)

    def _atomic_private_json(self, payload: Mapping[str, Any]) -> None:
        self._ensure_root()
        fd, raw_path = tempfile.mkstemp(prefix=".flows-", dir=self.root)
        temp_path = Path(raw_path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _ensure_root(self) -> None:
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise IntegrationFlowError("integration flow root is unsafe")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
