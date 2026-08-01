"""Exact local Visual QA target identity tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from gigaloom.automation.evaluations.visual import runtime as runtime_module
from gigaloom.automation.evaluations.visual.runtime import (
    _git_source_revision,
    _origin_and_port,
    _parse_lsof_listener_process_ids,
    _proc_listener_address,
    _process_descriptor_digest,
)


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_origin_validation_precedes_listener_inspection():
    assert _origin_and_port("http://127.0.0.1:39001/app") == (
        "http://127.0.0.1:39001",
        39001,
    )
    assert _origin_and_port("https://[::1]/app") == ("https://[::1]", 443)

    for value in (
        "https://example.com/app",
        "http://127.0.0.1:39001/app?token=secret",
        "http://user:secret@127.0.0.1:39001/app",
        "http://127.0.0.1:39001/api-token/value",
    ):
        with pytest.raises(ValueError, match="loopback|secret-free"):
            _origin_and_port(value)


def test_listener_binding_must_be_exactly_loopback_only():
    assert _parse_lsof_listener_process_ids(
        b"p42\nn127.0.0.1:39001\n",
        host="127.0.0.1",
        port=39001,
    ) == {42}
    assert _parse_lsof_listener_process_ids(
        b"p42\nn[::1]:39001\n",
        host="localhost",
        port=39001,
    ) == {42}
    with pytest.raises(ValueError, match="loopback-only"):
        _parse_lsof_listener_process_ids(
            b"p42\nn*:39001\n",
            host="127.0.0.1",
            port=39001,
        )
    with pytest.raises(ValueError, match="loopback-only"):
        _parse_lsof_listener_process_ids(
            b"p42\nn0.0.0.0:39001\n",
            host="127.0.0.1",
            port=39001,
        )

    assert str(_proc_listener_address("0100007F", ipv6=False)) == "127.0.0.1"
    assert (
        str(
            _proc_listener_address(
                "00000000000000000000000001000000",
                ipv6=True,
            )
        )
        == "::1"
    )


def test_git_source_revision_binds_staged_worktree_and_untracked_bytes(
    tmp_path: Path,
):
    _git(tmp_path, "init", "-q")
    source = tmp_path / "app.txt"
    source.write_text("committed\n", encoding="utf-8")
    _git(tmp_path, "add", "app.txt")
    _git(
        tmp_path,
        "-c",
        "user.name=Visual QA",
        "-c",
        "user.email=visual@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )

    baseline = _git_source_revision(tmp_path)
    source.write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "app.txt")
    staged = _git_source_revision(tmp_path)
    source.write_text("worktree\n", encoding="utf-8")
    worktree = _git_source_revision(tmp_path)
    (tmp_path / "new.txt").write_text("untracked\n", encoding="utf-8")
    untracked = _git_source_revision(tmp_path)

    assert baseline is not None
    assert staged is not None
    assert worktree is not None
    assert untracked is not None
    assert baseline[0] == staged[0] == worktree[0] == untracked[0]
    assert len({baseline[1], staged[1], worktree[1], untracked[1]}) == 4


def test_process_descriptor_uses_stable_process_start_identity(
    monkeypatch: pytest.MonkeyPatch,
):
    if not Path(f"/proc/{os.getpid()}/stat").is_file():
        monkeypatch.setattr(
            runtime_module,
            "_run",
            lambda command: subprocess.CompletedProcess(
                command,
                0,
                b"fixed-start-time fixed-command",
                b"",
            ),
        )
    first = _process_descriptor_digest(os.getpid())
    sum(range(10_000))
    second = _process_descriptor_digest(os.getpid())

    assert first == second
    assert len(first) == 64
