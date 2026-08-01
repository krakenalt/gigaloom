"""Harbor-to-headless environment mapping tests."""

from __future__ import annotations

import pytest

from gigaloom.execution.headless import (
    HEADLESS_ENVIRONMENT_KEYS,
    headless_environment_contract_digest,
)
from gigaloom.integrations.harbor.environment import (
    HARBOR_HEADLESS_CONTRACT_DIGEST,
    HARBOR_HEADLESS_ENV_KEYS,
    HarborAdapterConfigurationError,
    build_harbor_run_layout,
    build_headless_command,
    build_headless_environment,
    parse_harbor_agent_environment,
    validate_harbor_instruction,
)


def test_harbor_projection_matches_frozen_secret_free_profile() -> None:
    config = parse_harbor_agent_environment(
        {
            "GIGALOOM_AGENT": "qwen-code",
            "GIGALOOM_ROUTE": "qwen-acp",
            "GIGALOOM_TIMEOUT_SECONDS": "120",
        },
        fallback_model="provider/model",
    )
    layout = build_harbor_run_layout(
        run_id="harbor-0123456789abcdef01234567",
        workspace="/workspace",
    )
    environment = build_headless_environment(config, layout)

    assert HARBOR_HEADLESS_ENV_KEYS == HEADLESS_ENVIRONMENT_KEYS
    assert HARBOR_HEADLESS_CONTRACT_DIGEST == headless_environment_contract_digest()
    assert tuple(environment) == HEADLESS_ENVIRONMENT_KEYS
    assert environment["GIGALOOM_MODEL"] == "provider/model"
    assert environment["GIGALOOM_RESULT_DIR"].startswith("/logs/agent/")
    assert environment["GIGALOOM_TASK_FILE"].startswith("/tmp/")
    assert not any(
        marker in key
        for key in environment
        for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")
    )


def test_arbitrary_or_secret_agent_environment_is_rejected_without_value() -> None:
    with pytest.raises(HarborAdapterConfigurationError) as unknown:
        parse_harbor_agent_environment(
            {"GIGALOOM_AGENT": "qwen-code", "HOME": "/untrusted"},
            fallback_model=None,
        )
    assert "/untrusted" not in str(unknown.value)

    with pytest.raises(HarborAdapterConfigurationError) as secret:
        parse_harbor_agent_environment(
            {
                "GIGALOOM_AGENT": "qwen-code",
                "OPENAI_API_KEY": "fixture-secret-value",
            },
            fallback_model=None,
        )
    assert "fixture-secret-value" not in str(secret.value)


def test_instruction_is_explicit_file_content_not_command_content() -> None:
    instruction = validate_harbor_instruction(
        "Inspect $(touch /tmp/not-allowed) and report findings"
    )
    config = parse_harbor_agent_environment(
        {"GIGALOOM_AGENT": "qwen-code"},
        fallback_model=None,
    )
    layout = build_harbor_run_layout(
        run_id="harbor-0123456789abcdef01234567",
        workspace="/workspace",
    )
    command = build_headless_command(config, layout)

    assert instruction not in command
    assert "--prompt-file" in command
    assert "</dev/null" in command
    assert layout.task_file in command
    assert layout.event_file in command
    assert layout.diagnostic_file in command
