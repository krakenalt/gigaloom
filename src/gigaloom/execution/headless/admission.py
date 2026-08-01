"""Prompt and filesystem admission for deterministic headless runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import TextIO

from gigaloom.contracts import (
    HeadlessCapsuleMode,
    HeadlessEventFormat,
    HeadlessPromptSourceKind,
    HeadlessPromptSourceV1,
)
from gigaloom.contracts.operational_validation import (
    validate_digest,
    validate_identity,
)


MAX_HEADLESS_PROMPT_BYTES = 1024 * 1024


class HeadlessAdmissionError(ValueError):
    """Content-free refusal raised before execution starts."""

    def __init__(self, reason_code: str, message: str) -> None:
        validate_identity(reason_code, field_name="headless admission reason")
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HeadlessRunInput:
    """Explicit untrusted inputs for one headless invocation."""

    run_id: str
    agent_id: str
    route_id: str | None
    model_id: str | None
    workspace: str
    result_dir: str
    positional_prompt: str | None = field(repr=False)
    prompt_file: str | None
    prompt_stdin: bool
    timeout_seconds: int
    permission_profile: str
    network_profile: str
    capsule_mode: HeadlessCapsuleMode
    environment_contract_digest: str
    event_format: HeadlessEventFormat = HeadlessEventFormat.JSONL_V1
    no_input: bool = True

    def __post_init__(self) -> None:
        validate_identity(self.run_id, field_name="headless input run id")
        validate_identity(self.agent_id, field_name="headless input agent id")
        for value, field_name in (
            (self.route_id, "headless input route id"),
            (self.model_id, "headless input model id"),
        ):
            if value is not None:
                validate_identity(value, field_name=field_name)
        validate_digest(
            self.environment_contract_digest,
            field_name="headless input environment digest",
        )
        if not isinstance(self.prompt_stdin, bool):
            raise ValueError("headless prompt_stdin must be boolean")


@dataclass(frozen=True, slots=True)
class HeadlessPathAuthority:
    """Exact roots independently granted for task, workspace, and results."""

    workspace_roots: tuple[Path, ...]
    result_roots: tuple[Path, ...]
    prompt_roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        for attribute, field_name in (
            ("workspace_roots", "headless workspace authority"),
            ("result_roots", "headless result authority"),
            ("prompt_roots", "headless prompt authority"),
        ):
            roots = getattr(self, attribute)
            if not isinstance(roots, tuple) or (
                attribute != "prompt_roots" and not roots
            ):
                raise ValueError(f"{field_name} requires at least one root")
            normalized = tuple(sorted({_existing_directory(item) for item in roots}))
            object.__setattr__(self, attribute, normalized)

    def admit_workspace(self, value: str) -> Path:
        """Resolve an existing workspace under a granted root."""
        return _admit_existing_directory(
            value,
            roots=self.workspace_roots,
            reason_code="workspace_not_admitted",
            label="workspace",
        )

    def admit_prompt_file(self, value: str) -> Path:
        """Resolve an existing regular task file under a granted root."""
        candidate = _resolve_path(value, strict=True, label="prompt file")
        if not candidate.is_file() or not _under_any(candidate, self.prompt_roots):
            raise HeadlessAdmissionError(
                "prompt_file_not_admitted",
                "headless prompt file is outside the admitted roots",
            )
        return candidate

    def admit_result_dir(self, value: str) -> Path:
        """Resolve a prospective result directory under a granted root."""
        candidate = _resolve_path(value, strict=False, label="result directory")
        if not _under_any(candidate, self.result_roots):
            raise HeadlessAdmissionError(
                "result_dir_not_admitted",
                "headless result directory is outside the admitted roots",
            )
        return candidate

    def create_result_dir(self, candidate: Path) -> Path:
        """Create a previously admitted result directory and revalidate it."""
        if not _under_any(candidate, self.result_roots):
            raise HeadlessAdmissionError(
                "result_dir_not_admitted",
                "headless result directory is outside the admitted roots",
            )
        try:
            candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as error:
            raise HeadlessAdmissionError(
                "result_dir_unavailable",
                "headless result directory cannot be created",
            ) from error
        resolved = _resolve_path(str(candidate), strict=True, label="result directory")
        if not resolved.is_dir() or not _under_any(resolved, self.result_roots):
            raise HeadlessAdmissionError(
                "result_dir_not_admitted",
                "headless result directory changed during admission",
            )
        return resolved


@dataclass(frozen=True, slots=True)
class AdmittedPrompt:
    """Prompt content and its public content-free source binding."""

    content: str
    source: HeadlessPromptSourceV1


def admit_prompt(
    request: HeadlessRunInput,
    *,
    authority: HeadlessPathAuthority,
    stdin: TextIO | None,
) -> AdmittedPrompt:
    """Read exactly one bounded prompt source without ambient fallback."""
    selected = sum(
        (
            request.positional_prompt is not None,
            request.prompt_file is not None,
            request.prompt_stdin,
        )
    )
    if selected != 1:
        raise HeadlessAdmissionError(
            "prompt_source_count",
            "headless execution requires exactly one prompt source",
        )
    reference: str | None = None
    if request.positional_prompt is not None:
        kind = HeadlessPromptSourceKind.POSITIONAL
        content = request.positional_prompt
    elif request.prompt_file is not None:
        kind = HeadlessPromptSourceKind.FILE
        prompt_path = authority.admit_prompt_file(request.prompt_file)
        reference = prompt_path.as_posix()
        try:
            with prompt_path.open("rb") as stream:
                raw = stream.read(MAX_HEADLESS_PROMPT_BYTES + 1)
        except OSError as error:
            raise HeadlessAdmissionError(
                "prompt_file_unreadable",
                "headless prompt file cannot be read",
            ) from error
        if len(raw) > MAX_HEADLESS_PROMPT_BYTES:
            raise HeadlessAdmissionError(
                "prompt_too_large",
                "headless prompt exceeds the byte limit",
            )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise HeadlessAdmissionError(
                "prompt_not_utf8",
                "headless prompt file must be UTF-8",
            ) from error
    else:
        kind = HeadlessPromptSourceKind.STDIN
        if stdin is None:
            raise HeadlessAdmissionError(
                "stdin_unavailable",
                "explicit prompt stdin is unavailable",
            )
        isatty = getattr(stdin, "isatty", None)
        if callable(isatty) and bool(isatty()):
            raise HeadlessAdmissionError(
                "stdin_is_tty",
                "headless prompt stdin cannot be an interactive TTY",
            )
        try:
            content = stdin.read(MAX_HEADLESS_PROMPT_BYTES + 1)
        except OSError as error:
            raise HeadlessAdmissionError(
                "stdin_unreadable",
                "headless prompt stdin cannot be read",
            ) from error
    encoded = _validate_prompt_content(content)
    return AdmittedPrompt(
        content=content,
        source=HeadlessPromptSourceV1(
            kind=kind,
            content_digest=hashlib.sha256(encoded).hexdigest(),
            reference=reference,
        ),
    )


def _validate_prompt_content(value: object) -> bytes:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise HeadlessAdmissionError(
            "prompt_invalid",
            "headless prompt must be non-empty text without NUL bytes",
        )
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_HEADLESS_PROMPT_BYTES:
        raise HeadlessAdmissionError(
            "prompt_too_large",
            "headless prompt exceeds the byte limit",
        )
    return encoded


def _existing_directory(value: Path) -> Path:
    candidate = _resolve_path(str(value), strict=True, label="authority root")
    if not candidate.is_dir():
        raise ValueError("headless authority root must be a directory")
    return candidate


def _admit_existing_directory(
    value: str,
    *,
    roots: tuple[Path, ...],
    reason_code: str,
    label: str,
) -> Path:
    candidate = _resolve_path(value, strict=True, label=label)
    if not candidate.is_dir() or not _under_any(candidate, roots):
        raise HeadlessAdmissionError(
            reason_code,
            f"headless {label} is outside the admitted roots",
        )
    return candidate


def _resolve_path(value: str, *, strict: bool, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise HeadlessAdmissionError(
            "path_invalid",
            f"headless {label} path is invalid",
        )
    path = Path(value)
    if not path.is_absolute():
        raise HeadlessAdmissionError(
            "path_not_absolute",
            f"headless {label} path must be absolute",
        )
    try:
        return path.resolve(strict=strict)
    except OSError as error:
        raise HeadlessAdmissionError(
            "path_unavailable",
            f"headless {label} path is unavailable",
        ) from error


def _under_any(candidate: Path, roots: tuple[Path, ...]) -> bool:
    return any(candidate == root or candidate.is_relative_to(root) for root in roots)


__all__ = [
    "MAX_HEADLESS_PROMPT_BYTES",
    "AdmittedPrompt",
    "HeadlessAdmissionError",
    "HeadlessPathAuthority",
    "HeadlessRunInput",
    "admit_prompt",
]
