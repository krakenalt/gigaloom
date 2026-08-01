"""Versioned secret-free evaluation environment profile."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping

from gigaloom.contracts import (
    HeadlessCapsuleMode,
    HeadlessEventFormat,
)
from gigaloom.contracts.headless import MAX_HEADLESS_TIMEOUT_SECONDS
from gigaloom.contracts.operational_validation import (
    canonical_digest,
    validate_absolute_path,
    validate_identity,
    validate_text,
)
from gigaloom.execution.headless.admission import HeadlessRunInput


HEADLESS_ENVIRONMENT_SCHEMA_VERSION = 1
HEADLESS_ENVIRONMENT_PROFILE = "harbor"
HEADLESS_ENVIRONMENT_KEYS = (
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
_KEY_SET = frozenset(HEADLESS_ENVIRONMENT_KEYS)
_SECRET_KEY_RE = re.compile(
    r"(?:API[_-]?KEY|CREDENTIAL|PASSWORD|PASSWD|PRIVATE[_-]?KEY|SECRET|TOKEN)",
    re.IGNORECASE,
)


class UnknownEnvironmentPolicy(str, Enum):
    """Versioned treatment of unknown GigaLoom environment variables."""

    REJECT = "reject"
    IGNORE = "ignore"


class HeadlessEnvironmentError(ValueError):
    """Content-free evaluation environment validation failure."""

    def __init__(self, reason_code: str, message: str) -> None:
        validate_identity(reason_code, field_name="headless environment reason")
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HeadlessEnvironmentV1:
    """Exact configuration selected from the evaluation process environment."""

    agent_id: str
    route_id: str
    model_id: str
    workspace: str
    task_file: str
    result_dir: str
    run_id: str
    timeout_seconds: int
    permission_profile: str
    network_profile: str
    capsule_mode: HeadlessCapsuleMode
    data_dir: str
    event_format: HeadlessEventFormat = HeadlessEventFormat.JSONL_V1
    schema_version: int = HEADLESS_ENVIRONMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != HEADLESS_ENVIRONMENT_SCHEMA_VERSION:
            raise ValueError("unsupported headless environment schema_version")
        for value, field_name in (
            (self.agent_id, "headless environment agent id"),
            (self.route_id, "headless environment route id"),
            (self.model_id, "headless environment model id"),
            (self.run_id, "headless environment run id"),
            (self.permission_profile, "headless environment permission profile"),
            (self.network_profile, "headless environment network profile"),
        ):
            validate_identity(value, field_name=field_name)
        for value, field_name in (
            (self.workspace, "headless environment workspace"),
            (self.task_file, "headless environment task file"),
            (self.result_dir, "headless environment result directory"),
            (self.data_dir, "headless environment data directory"),
        ):
            validate_absolute_path(value, field_name=field_name)
        if self.workspace == self.result_dir:
            raise ValueError("headless environment workspace and result must differ")
        if self.task_file == self.result_dir:
            raise ValueError("headless environment task and result must differ")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= MAX_HEADLESS_TIMEOUT_SECONDS
        ):
            raise ValueError("headless environment timeout is invalid")
        if self.event_format is not HeadlessEventFormat.JSONL_V1:
            raise ValueError("headless environment event format is invalid")
        if not isinstance(self.capsule_mode, HeadlessCapsuleMode):
            raise ValueError("headless environment capsule mode is invalid")

    @property
    def environment_digest(self) -> str:
        """Return a content binding without exposing path or identity values."""
        return canonical_digest(self.to_environment())

    def to_environment(self) -> dict[str, str]:
        """Return exactly the secret-free allowlisted process configuration."""
        return {
            "GIGALOOM_HEADLESS": "1",
            "GIGALOOM_AGENT": self.agent_id,
            "GIGALOOM_ROUTE": self.route_id,
            "GIGALOOM_MODEL": self.model_id,
            "GIGALOOM_WORKSPACE": self.workspace,
            "GIGALOOM_TASK_FILE": self.task_file,
            "GIGALOOM_RESULT_DIR": self.result_dir,
            "GIGALOOM_EVENT_FORMAT": self.event_format.value,
            "GIGALOOM_RUN_ID": self.run_id,
            "GIGALOOM_TIMEOUT_SECONDS": str(self.timeout_seconds),
            "GIGALOOM_PERMISSION_PROFILE": self.permission_profile,
            "GIGALOOM_NETWORK_PROFILE": self.network_profile,
            "GIGALOOM_CAPSULE_MODE": self.capsule_mode.value,
            "GIGALOOM_DATA_DIR": self.data_dir,
        }

    def to_run_input(self) -> HeadlessRunInput:
        """Project configuration into untrusted input for separate admission."""
        return HeadlessRunInput(
            run_id=self.run_id,
            agent_id=self.agent_id,
            route_id=self.route_id,
            model_id=self.model_id,
            workspace=self.workspace,
            result_dir=self.result_dir,
            positional_prompt=None,
            prompt_file=self.task_file,
            prompt_stdin=False,
            timeout_seconds=self.timeout_seconds,
            permission_profile=self.permission_profile,
            network_profile=self.network_profile,
            capsule_mode=self.capsule_mode,
            environment_contract_digest=headless_environment_contract_digest(),
            event_format=self.event_format,
            no_input=True,
        )


@dataclass(frozen=True, slots=True)
class HeadlessEnvironmentDoctorReportV1:
    """Content-free readiness report for one environment projection."""

    ready: bool
    issue_codes: tuple[str, ...]
    contract_digest: str
    environment_digest: str | None
    known_key_count: int
    schema_version: int = HEADLESS_ENVIRONMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        """Return a stable public report without environment values."""
        return {
            "schema_version": self.schema_version,
            "profile": HEADLESS_ENVIRONMENT_PROFILE,
            "ready": self.ready,
            "issue_codes": list(self.issue_codes),
            "contract_digest": self.contract_digest,
            "environment_digest": self.environment_digest,
            "known_key_count": self.known_key_count,
            "content_free": True,
        }


def parse_headless_environment(
    environment: Mapping[str, object],
    *,
    unknown_policy: UnknownEnvironmentPolicy = UnknownEnvironmentPolicy.REJECT,
) -> HeadlessEnvironmentV1:
    """Decode the allowlisted profile without granting filesystem authority."""
    if not isinstance(unknown_policy, UnknownEnvironmentPolicy):
        raise HeadlessEnvironmentError(
            "unknown_policy_invalid",
            "headless unknown-variable policy is invalid",
        )
    if any(not isinstance(key, str) for key in environment):
        raise HeadlessEnvironmentError(
            "environment_key_invalid",
            "headless environment keys must be text",
        )
    typed = {str(key): value for key, value in environment.items()}
    prefixed = {key for key in typed if key.startswith("GIGALOOM_")}
    unknown = prefixed - _KEY_SET
    secret_like = {key for key in unknown if _SECRET_KEY_RE.search(key)}
    if secret_like:
        raise HeadlessEnvironmentError(
            "secret_variable_forbidden",
            "credential-like variables are forbidden in the headless contract",
        )
    if unknown and unknown_policy is UnknownEnvironmentPolicy.REJECT:
        raise HeadlessEnvironmentError(
            "unknown_variable",
            "unknown GigaLoom variables are not admitted by this profile",
        )
    missing = _KEY_SET - typed.keys()
    if missing:
        raise HeadlessEnvironmentError(
            "required_variable_missing",
            "the headless environment is missing required configuration",
        )
    values = {key: _environment_value(typed[key], key=key) for key in _KEY_SET}
    if values["GIGALOOM_HEADLESS"] != "1":
        raise HeadlessEnvironmentError(
            "headless_marker_invalid",
            "GIGALOOM_HEADLESS must select the versioned headless contract",
        )
    if values["GIGALOOM_EVENT_FORMAT"] != HeadlessEventFormat.JSONL_V1.value:
        raise HeadlessEnvironmentError(
            "event_format_invalid",
            "the headless environment event format is unsupported",
        )
    try:
        timeout = int(values["GIGALOOM_TIMEOUT_SECONDS"], 10)
    except ValueError as error:
        raise HeadlessEnvironmentError(
            "timeout_invalid",
            "the headless environment timeout is invalid",
        ) from error
    if str(timeout) != values["GIGALOOM_TIMEOUT_SECONDS"]:
        raise HeadlessEnvironmentError(
            "timeout_invalid",
            "the headless environment timeout is not canonical",
        )
    try:
        return HeadlessEnvironmentV1(
            agent_id=values["GIGALOOM_AGENT"],
            route_id=values["GIGALOOM_ROUTE"],
            model_id=values["GIGALOOM_MODEL"],
            workspace=values["GIGALOOM_WORKSPACE"],
            task_file=values["GIGALOOM_TASK_FILE"],
            result_dir=values["GIGALOOM_RESULT_DIR"],
            run_id=values["GIGALOOM_RUN_ID"],
            timeout_seconds=timeout,
            permission_profile=values["GIGALOOM_PERMISSION_PROFILE"],
            network_profile=values["GIGALOOM_NETWORK_PROFILE"],
            capsule_mode=HeadlessCapsuleMode(values["GIGALOOM_CAPSULE_MODE"]),
            data_dir=values["GIGALOOM_DATA_DIR"],
        )
    except (ValueError, TypeError) as error:
        raise HeadlessEnvironmentError(
            "environment_value_invalid",
            "the headless environment contains invalid configuration",
        ) from error


def doctor_headless_environment(
    environment: Mapping[str, object],
    *,
    unknown_policy: UnknownEnvironmentPolicy = UnknownEnvironmentPolicy.REJECT,
) -> HeadlessEnvironmentDoctorReportV1:
    """Validate an environment and retain only content-free evidence."""
    known_count = sum(
        isinstance(key, str) and key in _KEY_SET for key in environment.keys()
    )
    try:
        profile = parse_headless_environment(
            environment,
            unknown_policy=unknown_policy,
        )
    except HeadlessEnvironmentError as error:
        return HeadlessEnvironmentDoctorReportV1(
            ready=False,
            issue_codes=(error.reason_code,),
            contract_digest=headless_environment_contract_digest(),
            environment_digest=None,
            known_key_count=known_count,
        )
    return HeadlessEnvironmentDoctorReportV1(
        ready=True,
        issue_codes=(),
        contract_digest=headless_environment_contract_digest(),
        environment_digest=profile.environment_digest,
        known_key_count=known_count,
    )


def headless_environment_contract() -> dict[str, object]:
    """Return the frozen public contract used by introspection and digesting."""
    return {
        "schema_version": HEADLESS_ENVIRONMENT_SCHEMA_VERSION,
        "profile": HEADLESS_ENVIRONMENT_PROFILE,
        "required_keys": list(HEADLESS_ENVIRONMENT_KEYS),
        "event_format": HeadlessEventFormat.JSONL_V1.value,
        "headless_marker": "1",
        "unknown_variable_policies": [item.value for item in UnknownEnvironmentPolicy],
        "credentials_allowed": False,
        "environment_is_authority": False,
        "task_and_result_roots_are_independent": True,
    }


def headless_environment_contract_digest() -> str:
    """Return the canonical digest bound into every environment invocation."""
    return canonical_digest(headless_environment_contract())


def headless_environment_template() -> HeadlessEnvironmentV1:
    """Return a safe copyable Harbor-like template with no machine state."""
    return HeadlessEnvironmentV1(
        agent_id="agent-id",
        route_id="route-id",
        model_id="model-id",
        workspace="/workspace",
        task_file="/input/instruction.md",
        result_dir="/output/gigaloom",
        run_id="run-id",
        timeout_seconds=600,
        permission_profile="unattended",
        network_profile="none",
        capsule_mode=HeadlessCapsuleMode.EXPORT,
        data_dir="/output/gigaloom-data",
    )


def render_headless_dotenv(profile: HeadlessEnvironmentV1) -> str:
    """Render deterministic dotenv containing only allowlisted configuration."""
    environment = profile.to_environment()
    lines = [
        f'{key}="{_dotenv_escape(environment[key])}"'
        for key in HEADLESS_ENVIRONMENT_KEYS
    ]
    rendered = "\n".join(lines) + "\n"
    if "\x1b" in rendered or any(_SECRET_KEY_RE.search(key) for key in environment):
        raise ValueError("headless dotenv contains forbidden data")
    return rendered


def _environment_value(value: object, *, key: str) -> str:
    try:
        return validate_text(value, field_name=key, max_chars=2_048)
    except ValueError as error:
        raise HeadlessEnvironmentError(
            "environment_value_invalid",
            "the headless environment contains an invalid value",
        ) from error


def _dotenv_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


__all__ = [
    "HEADLESS_ENVIRONMENT_KEYS",
    "HEADLESS_ENVIRONMENT_PROFILE",
    "HEADLESS_ENVIRONMENT_SCHEMA_VERSION",
    "HeadlessEnvironmentDoctorReportV1",
    "HeadlessEnvironmentError",
    "HeadlessEnvironmentV1",
    "UnknownEnvironmentPolicy",
    "doctor_headless_environment",
    "headless_environment_contract",
    "headless_environment_contract_digest",
    "headless_environment_template",
    "parse_headless_environment",
    "render_headless_dotenv",
]
