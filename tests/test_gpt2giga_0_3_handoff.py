from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HANDOFF = json.loads(
    (REPOSITORY_ROOT / "tests/fixtures/gateway/gpt2giga_0_3_handoff.json").read_text(
        encoding="utf-8"
    )
)
MODEL = "GigaChat-2-Max"


def test_locked_registry_artifact_matches_the_0_3_handoff() -> None:
    import tomllib

    metadata = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    lock = tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    gateway = packages["gpt2giga"]
    artifact = HANDOFF["artifact"]

    assert metadata["project"]["optional-dependencies"]["gpt2giga"] == [
        "gpt2giga>=0.3.0,<0.4.0"
    ]
    assert gateway["version"] == artifact["version"]
    assert gateway["source"] == {"registry": "https://pypi.org/simple"}
    assert gateway["sdist"]["hash"] == f"sha256:{artifact['sdist_sha256']}"
    assert {
        wheel["hash"] for wheel in gateway["wheels"] if wheel["url"].endswith(".whl")
    } == {f"sha256:{artifact['wheel_sha256']}"}
    assert "sources" not in metadata.get("tool", {}).get("uv", {})


def test_installed_gateway_exposes_only_the_public_distribution_boundary() -> None:
    artifact = HANDOFF["artifact"]
    distribution = _installed_gateway_distribution()
    scripts = {
        entry.name: entry.value
        for entry in distribution.entry_points
        if entry.group == "console_scripts"
    }

    assert distribution.version == artifact["version"]
    assert distribution.metadata["Requires-Python"] == artifact["requires_python"]
    assert scripts == {"gpt2giga": artifact["entry_point"]}
    assert distribution.read_text("direct_url.json") is None


def test_inspect_config_is_content_free_and_network_free(tmp_path: Path) -> None:
    contracts = HANDOFF["contracts"]
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        "import socket\n"
        "def blocked(*_args, **_kwargs):\n"
        "    raise RuntimeError('network access is forbidden during preflight')\n"
        "socket.socket.connect = blocked\n"
        "socket.socket.connect_ex = blocked\n"
        "socket.create_connection = blocked\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [_gateway_executable(), "--inspect-config"],
        cwd=tmp_path,
        env={
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "PYTHONPATH": str(tmp_path),
            "GIGACHAT_MODEL": MODEL,
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    manifest = json.loads(result.stdout)
    serialized = result.stdout.lower()

    assert manifest["schema_version"] == contracts["inspect_schema"]
    assert manifest["profile_schema_version"] == contracts["provider_profile_schema"]
    assert manifest["config_revision"] == contracts["default_config_revision"]
    assert manifest["matrix_revision"] == contracts["loss_matrix_revision"]
    assert (
        manifest["profiles"][0]["profile_revision"]
        == contracts["default_profile_revision"]
    )
    assert manifest["profiles"][0]["models"][0]["public_alias"] == MODEL
    assert all(
        forbidden not in serialized
        for forbidden in ("access_token", "api_key", "credential_value", "secret")
    )


class _FakeGigaChatHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, Any]] = []

    def do_GET(self) -> None:
        type(self).requests.append(("GET", self.path, None))
        if self.path.endswith("/models"):
            self._json(
                {
                    "object": "list",
                    "data": [{"id": MODEL, "object": "model", "owned_by": "sber"}],
                }
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        size = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(size) or b"{}")
        type(self).requests.append(("POST", self.path, payload))
        if self.path.endswith("/chat/completions"):
            self._json(
                {
                    "model": MODEL,
                    "created_at": 1785794400,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [{"text": "hermetic bridge ok"}],
                            "finish_reason": "stop",
                        }
                    ],
                    "finish_reason": "stop",
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 3,
                        "total_tokens": 7,
                    },
                }
            )
            return
        self.send_error(404)

    def _json(self, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_hermetic_codex_responses_route_and_shutdown(tmp_path: Path) -> None:
    contracts = HANDOFF["contracts"]
    route_contract = HANDOFF["codex_responses_gigachat"]
    _FakeGigaChatHandler.requests = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGigaChatHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever)
    upstream_thread.start()
    port = _available_port()
    output: list[str] = []
    started = threading.Event()
    shutdown_complete = threading.Event()
    process = subprocess.Popen(
        [
            _gateway_executable(),
            "--proxy.host",
            "127.0.0.1",
            "--proxy.port",
            str(port),
            "--proxy.gigachat-api-mode",
            "v2",
            "--proxy.normalization-mode",
            "on",
            "--gigachat.base-url",
            f"http://127.0.0.1:{upstream.server_port}/v1",
            "--gigachat.model",
            MODEL,
            "--gigachat.verify-ssl-certs",
            "false",
        ],
        cwd=tmp_path,
        env={
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "GIGACHAT_ACCESS_TOKEN": "hermetic-token",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def drain_output() -> None:
        if process.stdout is None:  # pragma: no cover - Popen contract
            return
        for line in process.stdout:
            output.append(line)
            if "Application startup complete" in line:
                started.set()
            if "Application shutdown complete" in line:
                shutdown_complete.set()

    output_thread = threading.Thread(target=drain_output)
    output_thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        assert started.wait(timeout=15), "".join(output)
        assert _request_json(f"{base_url}/health") == (200, None)
        assert _FakeGigaChatHandler.requests == []

        status, readiness = _request_json(f"{base_url}/ready")
        assert status == 503
        assert readiness["schema_version"] == contracts["readiness_schema"]
        assert _FakeGigaChatHandler.requests == []

        status, models = _request_json(f"{base_url}/models")
        assert status == 200
        assert models["data"][0]["id"] == MODEL
        status, readiness = _request_json(f"{base_url}/ready")
        assert status == 200
        assert readiness["ready"] is True

        status, matrix = _request_json(f"{base_url}/bridge/capabilities")
        assert status == 200
        assert matrix["schema_version"] == contracts["route_support_matrix_schema"]
        assert matrix["matrix_revision"] == contracts["loss_matrix_revision"]
        route = next(
            cell
            for cell in matrix["cells"]
            if cell["public_protocol"] == "openai_responses"
            and cell["upstream_provider"] == "gigachat"
        )
        assert {
            "public_protocol": route["public_protocol"],
            "upstream_provider": route["upstream_provider"],
            "support_status": route["status"],
            "reason_ids": route["reason_ids"],
            "evidence_ids": route["evidence_ids"],
            "client_version_window": route["client_version_window"],
            "provider_version_window": route["provider_version_window"],
        } == route_contract

        blocked = sorted(
            cell["upstream_provider"]
            for cell in matrix["cells"]
            if cell["public_protocol"] == "openai_responses"
            and cell["status"] == "blocked"
        )
        assert blocked == HANDOFF["known_blocked_openai_responses_upstreams"]

        query = urlencode(
            {"model": MODEL, "protocol": "openai_responses", "api_mode": "v2"}
        )
        status, capabilities = _request_json(f"{base_url}/bridge/capabilities?{query}")
        assert status == 200
        assert (
            capabilities["schema_version"] == contracts["effective_capabilities_schema"]
        )
        assert (
            capabilities["capability_revision"]
            == contracts["hermetic_capability_revision"]
        )

        status, response = _request_json(
            f"{base_url}/v2/responses",
            payload={"model": MODEL, "input": "Codex hermetic probe"},
        )
        assert status == 200
        assert response["object"] == "response"
        assert response["output"][0]["content"][0]["text"] == "hermetic bridge ok"
        assert _FakeGigaChatHandler.requests == [
            ("GET", "/v1/models", None),
            (
                "POST",
                "/v2/chat/completions",
                {
                    "model": MODEL,
                    "messages": [
                        {
                            "content": [{"text": "Codex hermetic probe"}],
                            "role": "user",
                        }
                    ],
                },
            ),
        ]
    finally:
        process.terminate()
        assert shutdown_complete.wait(timeout=10), "".join(output)
        process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)
        output_thread.join(timeout=5)

    assert process.returncode in {0, -signal.SIGTERM}
    assert not upstream_thread.is_alive()
    assert not output_thread.is_alive()


def _gateway_executable() -> str:
    executable = Path(sys.executable).with_name("gpt2giga")
    if not executable.is_file():
        pytest.skip("optional gpt2giga extra is not installed")
    return str(executable)


def _installed_gateway_distribution() -> importlib.metadata.Distribution:
    try:
        return importlib.metadata.distribution("gpt2giga")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("optional gpt2giga extra is not installed")


def _available_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _request_json(
    url: str,
    *,
    payload: object | None = None,
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"content-type": "application/json"} if body is not None else {},
    )
    try:
        with urlopen(request, timeout=3) as response:
            response_body = response.read()
            return (
                response.status,
                json.loads(response_body) if response_body else None,
            )
    except HTTPError as exc:
        response_body = exc.read()
        return exc.code, json.loads(response_body) if response_body else None
