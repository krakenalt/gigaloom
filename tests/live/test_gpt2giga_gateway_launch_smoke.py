"""Explicit opt-in smoke for one real gpt2giga-to-GigaChat response."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from typing import Any
from urllib.request import Request, urlopen

import pytest

from gigaloom.cli_commands.gateway_application import build_gateway_launch_application
from gigaloom.cli_commands.gateway_launch import parse_gateway_launch_argv
from gigaloom.config import HarnessConfig


pytestmark = [pytest.mark.integration, pytest.mark.live_gigachat, pytest.mark.slow]
RUN_FLAG = "GIGALOOM_RUN_GPT2GIGA_LIVE_SMOKE"
ACK_FLAG = "GIGALOOM_GPT2GIGA_LIVE_ACKNOWLEDGEMENT"
MODEL_ENV = "GIGALOOM_GPT2GIGA_LIVE_MODEL"
CREDENTIAL_ENV = "GIGACHAT_CREDENTIALS"
REQUIRED_ACKNOWLEDGEMENT = "provider-cost-and-data-approved"


def _configured(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip() or "REPLACE_WITH" in value:
        return None
    return value.strip()


@pytest.fixture(scope="module", autouse=True)
def require_explicit_live_gateway_authority() -> None:
    if os.getenv(RUN_FLAG) != "1":
        pytest.skip(f"set {RUN_FLAG}=1 for the live gpt2giga gateway smoke")
    if os.getenv(ACK_FLAG) != REQUIRED_ACKNOWLEDGEMENT:
        pytest.skip(
            f"set {ACK_FLAG}={REQUIRED_ACKNOWLEDGEMENT} after approving provider cost"
        )
    if _configured(MODEL_ENV) is None:
        pytest.skip(f"set {MODEL_ENV} to one reviewed public model alias")
    if _configured(CREDENTIAL_ENV) is None:
        pytest.skip(f"set {CREDENTIAL_ENV} to an explicit live secret")


def test_live_one_command_gateway_route_returns_one_response(tmp_path: Path) -> None:
    model = _configured(MODEL_ENV)
    assert model is not None
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    application = build_gateway_launch_application(
        HarnessConfig(
            proxy_url=base_url,
            api_key="gigaloom-live-smoke-key",
            data_dir=str(tmp_path / "state"),
            default_model=model,
        )
    )
    request = parse_gateway_launch_argv(
        ["--with", "gpt2giga", "--model", model, "codex"]
    )
    assert request is not None
    responses: list[dict[str, Any]] = []

    def exercise_provider(
        argv: tuple[str, ...],
        environment: dict[str, str] | Any,
    ) -> int:
        assert argv == ("codex",)
        assert Path(environment["CODEX_HOME"]).is_relative_to(tmp_path / "state")
        payload = json.dumps(
            {
                "model": model,
                "input": "Reply with exactly OK. Do not call tools.",
                "max_output_tokens": 16,
            }
        ).encode("utf-8")
        http_request = Request(
            f"{base_url}/v2/responses",
            data=payload,
            headers={
                "authorization": f"Bearer {environment['GPT2GIGA_API_KEY']}",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urlopen(http_request, timeout=45) as response:
            responses.append(json.loads(response.read()))
        return 0

    assert application.run(request, native_launcher=exercise_provider) == 0
    assert len(responses) == 1
    response = responses[0]
    assert response["object"] == "response"
    assert response["output"]
    assert response["output"][0]["content"][0]["text"].strip()


def _available_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])
