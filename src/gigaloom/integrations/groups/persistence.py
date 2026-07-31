# ruff: noqa: E402, F401, F403, F405
"""Internal grouped integration implementation slice."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .helpers import *  # noqa: F403
from .models import *  # noqa: F403


class _GroupPersistenceMixin:
    """Implementation slice for grouped integration transactions."""

    def _transition(
        self,
        record: IntegrationGroupRecord,
        status: IntegrationGroupStatus,
        *,
        approval_hash: str | None = None,
        repair_actions: tuple[str, ...] | None = None,
        error_code: str | None = None,
    ) -> IntegrationGroupRecord:
        updated = replace(
            record,
            status=status,
            approval_hash=(
                approval_hash if approval_hash is not None else record.approval_hash
            ),
            repair_actions=(
                repair_actions if repair_actions is not None else record.repair_actions
            ),
            error_code=error_code,
            updated_at=self._timestamp(),
        )
        self._put(updated)
        return updated

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _put(self, record: IntegrationGroupRecord) -> None:
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            records = self._read_unlocked()
            records[record.id] = record
            if len(records) > MAX_INTEGRATION_GROUPS:
                ordered = sorted(records.values(), key=lambda item: item.updated_at)
                records = {item.id: item for item in ordered[-MAX_INTEGRATION_GROUPS:]}
            self._write_unlocked(records)

    def _read(self) -> dict[str, IntegrationGroupRecord]:
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, IntegrationGroupRecord]:
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or not self.path.is_file():
            raise IntegrationGroupError("integration group state is unsafe")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IntegrationGroupError(
                "integration group state is unreadable"
            ) from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != INTEGRATION_GROUP_SCHEMA_VERSION
            or not isinstance(payload.get("groups"), list)
            or len(payload["groups"]) > MAX_INTEGRATION_GROUPS
        ):
            raise IntegrationGroupError("integration group state is invalid")
        records = tuple(_record_from_dict(item) for item in payload["groups"])
        if len({item.id for item in records}) != len(records):
            raise IntegrationGroupError("integration group state has duplicate ids")
        return {item.id: item for item in records}

    def _write_unlocked(self, records: Mapping[str, IntegrationGroupRecord]) -> None:
        payload = {
            "schema_version": INTEGRATION_GROUP_SCHEMA_VERSION,
            "groups": [_private_record(records[key]) for key in sorted(records)],
        }
        fd, raw_path = tempfile.mkstemp(prefix=".groups-", dir=self.root)
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
            raise IntegrationGroupError("integration group root is unsafe")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
