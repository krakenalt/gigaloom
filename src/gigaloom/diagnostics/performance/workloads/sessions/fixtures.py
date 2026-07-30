"""Direct content-free fixture producer kept outside measured windows."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from gigaloom.native import HarnessInvocationMode
from gigaloom.runtime.models import RunStatus
from gigaloom.sessions import FilesystemHarnessSessionStore
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessRun,
    HarnessSession,
    HarnessStoredEvent,
    event_to_dict,
    message_to_dict,
    run_to_dict,
    session_to_dict,
)
from gigaloom.types import GigaChatApiMode, HarnessCapability


_TIMESTAMP = "2026-01-01T00:00:00+00:00"


@dataclass(frozen=True, slots=True)
class SessionFixture:
    root: Path
    store: FilesystemHarnessSessionStore
    session_ids: tuple[str, ...]
    run_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    event_offsets: tuple[int, ...] = ()

    @property
    def session_id(self) -> str:
        return self.session_ids[0]


def build_catalog_fixture(root: Path, count: int) -> SessionFixture:
    _reset_root(root)
    sessions = tuple(_session(index) for index in range(count))
    for session in sessions:
        _write_json(
            _session_dir(root, session.id) / "manifest.json", session_to_dict(session)
        )
    _write_index(root, sessions)
    return SessionFixture(
        root=root,
        store=FilesystemHarnessSessionStore(root),
        session_ids=tuple(session.id for session in sessions),
    )


def build_history_fixture(
    root: Path,
    *,
    messages: int = 0,
    runs: int = 0,
    events: int = 0,
) -> SessionFixture:
    _reset_root(root)
    session = _session(0)
    session_dir = _session_dir(root, session.id)
    _write_json(session_dir / "manifest.json", session_to_dict(session))
    _write_index(root, (session,))
    run_rows = tuple(
        _run(session.id, index) for index in range(runs or int(events > 0))
    )
    if messages:
        _write_jsonl(
            session_dir / "messages.jsonl",
            (message_to_dict(_message(session.id, index)) for index in range(messages)),
        )
    if run_rows:
        _write_jsonl(session_dir / "runs.jsonl", map(run_to_dict, run_rows))
    event_ids: list[str] = []
    event_offsets: list[int] = []
    if events:
        path = session_dir / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            for index in range(events):
                event = fixture_event(session.id, run_rows[0].id, index)
                event_ids.append(event.id)
                event_offsets.append(handle.tell())
                encoded = (
                    json.dumps(
                        event_to_dict(event), ensure_ascii=True, separators=(",", ":")
                    )
                    + "\n"
                ).encode()
                handle.write(encoded)
    return SessionFixture(
        root=root,
        store=FilesystemHarnessSessionStore(root),
        session_ids=(session.id,),
        run_ids=tuple(run.id for run in run_rows),
        event_ids=tuple(event_ids),
        event_offsets=tuple(event_offsets),
    )


def _reset_root(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)


def _session(index: int) -> HarnessSession:
    return HarnessSession(
        id=f"sess_fixture_{index:06d}",
        title=f"Fixture {index:06d}",
        created_at=_TIMESTAMP,
        updated_at=f"2026-01-01T00:00:{index % 60:02d}+00:00",
        workspace=None,
        default_harness_id="echo",
        default_model=None,
        default_api_mode=GigaChatApiMode.V2,
        default_mode="read",
    )


def _message(session_id: str, index: int) -> HarnessMessage:
    return HarnessMessage(
        id=f"msg_fixture_{index:06d}",
        session_id=session_id,
        run_id=None,
        role="assistant",
        content="content-free",
        created_at=_TIMESTAMP,
    )


def _run(session_id: str, index: int) -> HarnessRun:
    return HarnessRun(
        id=f"run_fixture_{index:06d}",
        session_id=session_id,
        harness_id="echo",
        status=RunStatus.QUEUED,
        prompt="content-free",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="read",
        workspace=None,
        created_at=_TIMESTAMP,
        updated_at=_TIMESTAMP,
        invocation_mode=HarnessInvocationMode.HEADLESS,
    )


def fixture_event(
    session_id: str,
    run_id: str,
    index: int,
) -> HarnessStoredEvent:
    return HarnessStoredEvent(
        id=f"evt_fixture_{index:06d}",
        session_id=session_id,
        run_id=run_id,
        type="delta",
        message="content-free",
        payload={"sequence": index},
        created_at=_TIMESTAMP,
        sequence=index,
    )


def _session_dir(root: Path, session_id: str) -> Path:
    return root / "sessions" / "2026" / "01" / session_id


def _write_index(root: Path, sessions: tuple[HarnessSession, ...]) -> None:
    _write_json(
        root / "sessions" / "index.json",
        {
            "sessions": [
                {"id": session.id, "path": f"2026/01/{session.id}"}
                for session in sessions
            ]
        },
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, payloads: Iterable[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
            handle.write("\n")
