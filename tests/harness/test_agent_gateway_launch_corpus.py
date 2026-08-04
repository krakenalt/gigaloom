"""Pinned installed-agent and public-gateway launch corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gigaloom.native.launch.gateway_profile import (
    GPT2GIGA_CAPABILITIES_CONTRACT_REVISION,
    GPT2GIGA_DISTRIBUTION,
    GPT2GIGA_INSPECT_CONTRACT_REVISION,
    GPT2GIGA_LOSS_MATRIX_REVISION,
    GPT2GIGA_MODELS_CONTRACT_REVISION,
    GPT2GIGA_PROVIDER_PROFILE_REVISION,
    GPT2GIGA_READINESS_CONTRACT_REVISION,
    GPT2GIGA_STARTUP_CONFIG_REVISION,
    GPT2GIGA_VERSION,
    GPT2GIGA_VERSION_WINDOW,
    GPT2GIGA_WHEEL_SHA256,
)


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
CORPUS_PATH = FIXTURE_ROOT / "gateway" / "agent_gateway_launch_corpus_v1.json"
HANDOFF_PATH = FIXTURE_ROOT / "gateway" / "gpt2giga_0_3_handoff.json"
NATIVE_INVENTORY_PATH = FIXTURE_ROOT / "native_cli_contracts" / "command_inventory.json"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _case_map(corpus: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = corpus["cases"]
    assert isinstance(cases, list)
    assert len(cases) == 4
    result = {case["case_id"]: case for case in cases}
    assert len(result) == len(cases)
    return result


def test_corpus_pins_exact_public_gpt2giga_artifact_and_revisions() -> None:
    corpus = _load(CORPUS_PATH)
    handoff = _load(HANDOFF_PATH)

    assert corpus["schema_version"] == "gigaloom.agent-gateway-launch-corpus.v1"
    assert corpus["corpus_id"] == "gpt2giga-0.3.0-agent-gateway-launch"
    assert corpus["content_free"] is True
    assert corpus["artifact"] == {
        **handoff["artifact"],
        "version_window": GPT2GIGA_VERSION_WINDOW,
    }
    assert corpus["artifact"]["distribution"] == GPT2GIGA_DISTRIBUTION
    assert corpus["artifact"]["version"] == GPT2GIGA_VERSION
    assert corpus["artifact"]["wheel_sha256"] == GPT2GIGA_WHEEL_SHA256

    contracts = corpus["contracts"]
    handoff_contracts = handoff["contracts"]
    assert contracts == {
        "inspect_schema": GPT2GIGA_INSPECT_CONTRACT_REVISION,
        "provider_profile_schema": GPT2GIGA_PROVIDER_PROFILE_REVISION,
        "startup_config_revision": GPT2GIGA_STARTUP_CONFIG_REVISION,
        "readiness_schema": GPT2GIGA_READINESS_CONTRACT_REVISION,
        "models_schema": GPT2GIGA_MODELS_CONTRACT_REVISION,
        "route_support_matrix_schema": GPT2GIGA_CAPABILITIES_CONTRACT_REVISION,
        "loss_matrix_revision": GPT2GIGA_LOSS_MATRIX_REVISION,
        "effective_capabilities_schema": handoff_contracts[
            "effective_capabilities_schema"
        ],
        "hermetic_capability_revision": handoff_contracts[
            "hermetic_capability_revision"
        ],
    }
    assert (
        contracts["startup_config_revision"]
        == handoff_contracts["default_config_revision"]
    )


def test_corpus_covers_the_four_frozen_agent_launch_outcomes() -> None:
    corpus = _load(CORPUS_PATH)
    cases = _case_map(corpus)

    assert set(cases) == {
        "codex-responses-gigachat",
        "claude-anthropic-gigachat",
        "managed-acp-model-selector",
        "gemini-native-custom-endpoint-blocked",
    }
    codex = cases["codex-responses-gigachat"]
    assert codex["client_protocol"] == "openai_responses"
    assert codex["gateway_client_version_window"] == (
        "codex-cli==0.146.0;openai-python==2.50.0"
    )
    assert codex["effective_support_status"] == "technical_preview"
    assert codex["expected_adapter"]["wire_api"] == "responses"

    claude = cases["claude-anthropic-gigachat"]
    assert claude["client_protocol"] == "anthropic_messages"
    assert claude["gateway_support_status"] == "technical_preview"
    assert claude["effective_support_status"] == "vendor_unsupported"
    assert claude["required_acknowledgement"] == ("acknowledge_vendor_unsupported")

    acp = cases["managed-acp-model-selector"]
    assert acp["client_protocol"] == "acp"
    assert acp["expected_adapter"]["advertised_selector_categories"] == [
        "model",
        "model_config",
        "thought_level",
    ]
    assert acp["expected_adapter"]["session_actions"] == [
        "session/new",
        "session/set_config_option",
        "session/prompt",
        "session/list",
    ]

    gemini = cases["gemini-native-custom-endpoint-blocked"]
    assert gemini["effective_support_status"] == "blocked"
    assert gemini["reason_ids"] == ["gemini_custom_endpoint_unsupported"]
    assert gemini["expected_adapter"]["process_spawn"] is False
    assert gemini["expected_adapter"]["provider_traffic"] is False
    assert all(
        case["expected_adapter"]["mutates_native_home"] is False
        for case in cases.values()
    )


def test_agent_pins_match_the_reviewed_native_contracts() -> None:
    cases = _case_map(_load(CORPUS_PATH))
    inventory = _load(NATIVE_INVENTORY_PATH)["providers"]

    claude = inventory["claude"]["integration"]
    gemini = inventory["gemini"]["integration"]
    assert cases["claude-anthropic-gigachat"]["pinned_agent_version"] == (
        claude["release_tag"].removeprefix("v")
    )
    assert cases["claude-anthropic-gigachat"]["agent_version_window"] == (
        f">={claude['version_window'][0]},<{claude['version_window'][1]}"
    )
    assert cases["gemini-native-custom-endpoint-blocked"][
        "pinned_agent_version"
    ] == gemini["release_tag"].removeprefix("v")
    assert (
        cases["gemini-native-custom-endpoint-blocked"]["agent_version_window"]
        == f">={gemini['version_window'][0]},<{gemini['version_window'][1]}"
    )


def test_corpus_contains_no_credentials_or_executable_authority() -> None:
    corpus = _load(CORPUS_PATH)
    serialized = json.dumps(corpus, sort_keys=True).lower()

    assert "credential_value" not in serialized
    assert "access_token" not in serialized
    assert "api_key_value" not in serialized
    assert all(len(case["selection"]) <= 8 for case in corpus["cases"])
