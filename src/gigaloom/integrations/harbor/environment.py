"""Secret-free mapping from Harbor agent configuration to headless inputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex
from typing import Mapping

from gigaloom.contracts import HeadlessCapsuleMode, HeadlessEventFormat
from gigaloom.contracts.headless import MAX_HEADLESS_TIMEOUT_SECONDS
from gigaloom.contracts.operational_validation import (
    validate_absolute_path,
    validate_digest,
    validate_identity,
    validate_text,
)


HARBOR_AGENT_ENV_KEYS = (
    "GIGALOOM_AGENT",
    "GIGALOOM_ROUTE",
    "GIGALOOM_MODEL",
    "GIGALOOM_TIMEOUT_SECONDS",
    "GIGALOOM_PERMISSION_PROFILE",
    "GIGALOOM_NETWORK_PROFILE",
    "GIGALOOM_CAPSULE_MODE",
)
HARBOR_HEADLESS_ENV_KEYS = (
    "GIGALOOM_HEADLESS",
    "GIGALOOM_AGENT",
    "GIGALOOM_ROUTE",
    "GIGALOOM_MODEL",
    "GIGALOOM_WORKSPACE",
    "GIGALOOM_TASK_FILE",
    "GIGALOOM_RESULT_DIR",
    "GIGALOOM_EVENT_FORMAT",
    "GIGALOOM_RUN_ID",
    "GIGALOOM_TIMEOUT_SECONDS",
    "GIGALOOM_PERMISSION_PROFILE",
    "GIGALOOM_NETWORK_PROFILE",
    "GIGALOOM_CAPSULE_MODE",
    "GIGALOOM_DATA_DIR",
)
HARBOR_HEADLESS_CONTRACT_DIGEST = (
    "9b72a215e90399086de218aed0c26585995f80cc37462c9d08c802fb67d9f196"
)
MAX_HARBOR_INSTRUCTION_BYTES = 1024 * 1024
_SECRET_KEY_RE = re.compile(
    r"(?:API[_-]?KEY|CREDENTIAL|PASSWORD|PASSWD|PRIVATE[_-]?KEY|SECRET|TOKEN)",
    re.IGNORECASE,
)
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


class HarborAdapterConfigurationError(ValueError):
    """Content-free refusal of unsafe or malformed Harbor agent config."""


@dataclass(frozen=True, slots=True)
class HarborAgentConfigurationV1:
    """The only Harbor agent environment values admitted by the adapter."""

    agent_id: str
    route_id: str | None
    model_id: str | None
    timeout_seconds: int
    permission_profile: str
    network_profile: str
    capsule_mode: HeadlessCapsuleMode
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported Harbor agent configuration schema")
        validate_identity(self.agent_id, field_name="Harbor agent id")
        for value, label in (
            (self.route_id, "Harbor route id"),
            (self.model_id, "Harbor model id"),
        ):
            if value is not None:
                validate_identity(value, field_name=label)
        validate_identity(
            self.permission_profile,
            field_name="Harbor permission profile",
        )
        validate_identity(self.network_profile, field_name="Harbor network profile")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= MAX_HEADLESS_TIMEOUT_SECONDS
        ):
            raise ValueError("Harbor timeout is invalid")
        if not isinstance(self.capsule_mode, HeadlessCapsuleMode):
            raise ValueError("Harbor capsule mode is invalid")


@dataclass(frozen=True, slots=True)
class HarborRunLayoutV1:
    """Exact container paths for one isolated Harbor headless invocation."""

    run_id: str
    workspace: str
    runtime_root: str
    log_root: str
    task_file: str
    result_dir: str
    event_file: str
    diagnostic_file: str
    data_dir: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported Harbor run layout schema")
        validate_identity(self.run_id, field_name="Harbor run id")
        for value, label in (
            (self.workspace, "Harbor workspace"),
            (self.runtime_root, "Harbor runtime root"),
            (self.log_root, "Harbor log root"),
            (self.task_file, "Harbor task file"),
            (self.result_dir, "Harbor result directory"),
            (self.event_file, "Harbor event file"),
            (self.diagnostic_file, "Harbor diagnostic file"),
            (self.data_dir, "Harbor data directory"),
        ):
            validate_absolute_path(value, field_name=label)
        if self.runtime_root == self.log_root or self.workspace == self.result_dir:
            raise ValueError("Harbor headless roots must be independent")
        runtime = PurePosixPath(self.runtime_root)
        logs = PurePosixPath(self.log_root)
        if (
            not PurePosixPath(self.task_file).is_relative_to(runtime)
            or not PurePosixPath(self.data_dir).is_relative_to(runtime)
            or not PurePosixPath(self.result_dir).is_relative_to(logs)
            or not PurePosixPath(self.event_file).is_relative_to(logs)
            or not PurePosixPath(self.diagnostic_file).is_relative_to(logs)
        ):
            raise ValueError("Harbor headless artifacts escaped their owned roots")

    @property
    def event_log_ref(self) -> str:
        """Return the event path relative to Harbor's agent log boundary."""
        return _relative_to_agent_logs(self.event_file)

    @property
    def result_log_ref(self) -> str:
        """Return the result path relative to Harbor's agent log boundary."""
        return _relative_to_agent_logs(self.result_dir)

    def prepare_command(self) -> str:
        """Return a collision-refusing POSIX setup command."""
        runtime = shlex.quote(self.runtime_root)
        logs = shlex.quote(self.log_root)
        data = shlex.quote(self.data_dir)
        return (
            "umask 077 && "
            f"test ! -e {runtime} && test ! -e {logs} && "
            f"mkdir -p {runtime} {logs} {data}"
        )

    def cleanup_command(self) -> str:
        """Return an exact prompt-file cleanup command."""
        return f"rm -f -- {shlex.quote(self.task_file)}"


def parse_harbor_agent_environment(
    environment: Mapping[str, object],
    *,
    fallback_model: str | None,
) -> HarborAgentConfigurationV1:
    """Reject every agent variable outside the explicit secret-free allowlist."""
    if any(not isinstance(key, str) for key in environment):
        raise HarborAdapterConfigurationError("Harbor agent env keys must be text")
    unknown = set(environment) - set(HARBOR_AGENT_ENV_KEYS)
    if unknown:
        if any(_SECRET_KEY_RE.search(str(key)) for key in unknown):
            raise HarborAdapterConfigurationError(
                "credential-bearing Harbor agent env is forbidden"
            )
        raise HarborAdapterConfigurationError("unknown Harbor agent env is forbidden")
    values = {
        key: _optional_environment_value(environment.get(key), key=key)
        for key in HARBOR_AGENT_ENV_KEYS
    }
    agent_id = values["GIGALOOM_AGENT"]
    if agent_id is None:
        raise HarborAdapterConfigurationError("GIGALOOM_AGENT is required")
    model_id = values["GIGALOOM_MODEL"] or fallback_model
    timeout = _timeout(values["GIGALOOM_TIMEOUT_SECONDS"])
    try:
        return HarborAgentConfigurationV1(
            agent_id=validate_identity(agent_id, field_name="Harbor agent id"),
            route_id=_optional_identity(values["GIGALOOM_ROUTE"], "Harbor route id"),
            model_id=_optional_identity(model_id, "Harbor model id"),
            timeout_seconds=timeout,
            permission_profile=validate_identity(
                values["GIGALOOM_PERMISSION_PROFILE"] or "unattended",
                field_name="Harbor permission profile",
            ),
            network_profile=validate_identity(
                values["GIGALOOM_NETWORK_PROFILE"] or "none",
                field_name="Harbor network profile",
            ),
            capsule_mode=HeadlessCapsuleMode(
                values["GIGALOOM_CAPSULE_MODE"] or HeadlessCapsuleMode.EXPORT.value
            ),
        )
    except (TypeError, ValueError) as error:
        raise HarborAdapterConfigurationError(
            "Harbor agent env contains invalid configuration"
        ) from error


def validate_harbor_instruction(value: object) -> str:
    """Validate one explicit bounded instruction without rendering its content."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise HarborAdapterConfigurationError("Harbor instruction is invalid")
    if len(value.encode("utf-8")) > MAX_HARBOR_INSTRUCTION_BYTES:
        raise HarborAdapterConfigurationError("Harbor instruction is too large")
    return value


def harbor_run_id(*, session_id: object, context_id: object) -> str:
    """Derive a stable content-free run identity from Harbor lifecycle identity."""
    material = f"{session_id or ''}\0{context_id or ''}"
    if not material.strip("\0"):
        raise HarborAdapterConfigurationError("Harbor run identity is unavailable")
    return f"harbor-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def build_harbor_run_layout(*, run_id: str, workspace: str) -> HarborRunLayoutV1:
    """Build independent input, data, and Harbor log-boundary paths."""
    validate_identity(run_id, field_name="Harbor run id")
    workspace = validate_absolute_path(workspace, field_name="Harbor workspace")
    runtime = PurePosixPath("/tmp/gigaloom-headless") / run_id
    logs = PurePosixPath("/logs/agent/gigaloom") / run_id
    return HarborRunLayoutV1(
        run_id=run_id,
        workspace=workspace,
        runtime_root=runtime.as_posix(),
        log_root=logs.as_posix(),
        task_file=(runtime / "instruction.md").as_posix(),
        result_dir=(logs / "result").as_posix(),
        event_file=(logs / "events.jsonl").as_posix(),
        diagnostic_file=(logs / "diagnostics.log").as_posix(),
        data_dir=(runtime / "data").as_posix(),
    )


def build_headless_environment(
    config: HarborAgentConfigurationV1,
    layout: HarborRunLayoutV1,
) -> dict[str, str]:
    """Return exactly the GigaLoom-owned, credential-free environment profile."""
    values = {
        "GIGALOOM_HEADLESS": "1",
        "GIGALOOM_AGENT": config.agent_id,
        "GIGALOOM_ROUTE": config.route_id or "auto",
        "GIGALOOM_MODEL": config.model_id or "auto",
        "GIGALOOM_WORKSPACE": layout.workspace,
        "GIGALOOM_TASK_FILE": layout.task_file,
        "GIGALOOM_RESULT_DIR": layout.result_dir,
        "GIGALOOM_EVENT_FORMAT": HeadlessEventFormat.JSONL_V1.value,
        "GIGALOOM_RUN_ID": layout.run_id,
        "GIGALOOM_TIMEOUT_SECONDS": str(config.timeout_seconds),
        "GIGALOOM_PERMISSION_PROFILE": config.permission_profile,
        "GIGALOOM_NETWORK_PROFILE": config.network_profile,
        "GIGALOOM_CAPSULE_MODE": config.capsule_mode.value,
        "GIGALOOM_DATA_DIR": layout.data_dir,
    }
    if tuple(values) != HARBOR_HEADLESS_ENV_KEYS:
        raise RuntimeError("Harbor environment projection drifted")
    return values


def build_headless_command(
    config: HarborAgentConfigurationV1,
    layout: HarborRunLayoutV1,
) -> str:
    """Build one inert, non-interactive command with stdout/stderr separation."""
    tokens = [
        "giga",
        "run",
        "--headless",
        "--agent",
        config.agent_id,
        "--workspace",
        layout.workspace,
        "--prompt-file",
        layout.task_file,
        "--events",
        "jsonl",
        "--result-dir",
        layout.result_dir,
        "--timeout",
        str(config.timeout_seconds),
        "--permission-profile",
        config.permission_profile,
        "--network-profile",
        config.network_profile,
        "--capsule-mode",
        config.capsule_mode.value,
        "--no-input",
    ]
    if config.route_id is not None:
        tokens.extend(("--route", config.route_id))
    if config.model_id is not None:
        tokens.extend(("--model", config.model_id))
    command = " ".join(shlex.quote(token) for token in tokens)
    return (
        f"exec {command} </dev/null >{shlex.quote(layout.event_file)} "
        f"2>{shlex.quote(layout.diagnostic_file)}"
    )


def parse_headless_contract_output(value: object) -> str:
    """Validate the container's headless introspection response."""
    if not isinstance(value, str):
        raise HarborAdapterConfigurationError("headless contract output must be text")
    text = value[:-1] if value.endswith("\n") else value
    try:
        text = validate_text(
            text,
            field_name="headless contract output",
            max_chars=65_536,
        )
    except ValueError as error:
        raise HarborAdapterConfigurationError(
            "headless contract output is invalid"
        ) from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise HarborAdapterConfigurationError(
            "headless contract output is not JSON"
        ) from error
    if not isinstance(payload, dict) or (
        payload.get("schema_version") != 1
        or payload.get("profile") != "harbor"
        or payload.get("event_format") != HeadlessEventFormat.JSONL_V1.value
        or payload.get("credentials_allowed") is not False
        or payload.get("environment_is_authority") is not False
        or tuple(payload.get("required_keys", ())) != HARBOR_HEADLESS_ENV_KEYS
    ):
        raise HarborAdapterConfigurationError("headless contract is incompatible")
    digest = payload.get("contract_digest")
    if (
        not isinstance(digest, str)
        or _DIGEST_RE.fullmatch(digest) is None
        or digest != HARBOR_HEADLESS_CONTRACT_DIGEST
    ):
        raise HarborAdapterConfigurationError("headless contract digest is invalid")
    return validate_digest(digest, field_name="headless contract digest")


def _optional_environment_value(value: object, *, key: str) -> str | None:
    if value is None:
        return None
    try:
        return validate_text(value, field_name=key, max_chars=2_048)
    except ValueError as error:
        raise HarborAdapterConfigurationError(
            "Harbor agent env contains an invalid value"
        ) from error


def _optional_identity(value: str | None, label: str) -> str | None:
    return None if value is None else validate_identity(value, field_name=label)


def _timeout(value: str | None) -> int:
    if value is None:
        return 600
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise HarborAdapterConfigurationError("Harbor timeout is invalid") from error
    if str(parsed) != value or not 1 <= parsed <= MAX_HEADLESS_TIMEOUT_SECONDS:
        raise HarborAdapterConfigurationError("Harbor timeout is invalid")
    return parsed


def _relative_to_agent_logs(value: str) -> str:
    path = PurePosixPath(value)
    try:
        return path.relative_to("/logs/agent").as_posix()
    except ValueError as error:
        raise ValueError("Harbor artifact is outside the agent log boundary") from error


__all__ = [
    "HARBOR_AGENT_ENV_KEYS",
    "HARBOR_HEADLESS_CONTRACT_DIGEST",
    "HARBOR_HEADLESS_ENV_KEYS",
    "MAX_HARBOR_INSTRUCTION_BYTES",
    "HarborAdapterConfigurationError",
    "HarborAgentConfigurationV1",
    "HarborRunLayoutV1",
    "build_harbor_run_layout",
    "build_headless_command",
    "build_headless_environment",
    "harbor_run_id",
    "parse_harbor_agent_environment",
    "parse_headless_contract_output",
    "validate_harbor_instruction",
]
