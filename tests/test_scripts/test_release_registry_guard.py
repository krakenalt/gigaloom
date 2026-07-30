from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
from threading import Thread
from typing import Any

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "release_registry_guard.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("release_registry_guard", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def candidate(tmp_path: Path) -> dict[str, Any]:
    artifacts = tmp_path / "candidate"
    artifacts.mkdir()
    wheel = artifacts / "gigaloom-0.6.0a1-py3-none-any.whl"
    sdist = artifacts / "gigaloom-0.6.0a1.tar.gz"
    npm = artifacts / "gigaloom-web-0.6.0-alpha.1.tgz"
    wheel.write_bytes(b"wheel candidate")
    sdist.write_bytes(b"sdist candidate")
    npm.write_bytes(b"npm candidate")
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {
                "git_tag": "v0.6.0-alpha.1",
                "npm_package": "@gigaloom/web",
                "npm_version": "0.6.0-alpha.1",
                "python_distribution": "gigaloom",
                "python_version": "0.6.0a1",
                "release": "0.6.0-alpha.1",
            }
        ),
        encoding="utf-8",
    )
    return {
        "artifact_dir": artifacts,
        "npm": npm,
        "release": release,
        "sdist": sdist,
        "wheel": wheel,
    }


def pypi_payload(candidate: dict[str, Any], *, exact: bool) -> dict[str, Any]:
    files = []
    for key in ("wheel", "sdist"):
        path = candidate[key]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not exact and key == "wheel":
            digest = "0" * 64
        files.append({"filename": path.name, "digests": {"sha256": digest}})
    return {"releases": {"0.6.0a1": files}}


def npm_payload(candidate: dict[str, Any], *, exact: bool) -> dict[str, Any]:
    content = candidate["npm"].read_bytes()
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode()
    if not exact:
        integrity = "sha512-" + base64.b64encode(b"wrong").decode()
    return {
        "versions": {
            "0.6.0-alpha.1": {
                "dist": {
                    "integrity": integrity,
                    "shasum": hashlib.sha1(content).hexdigest(),
                }
            }
        }
    }


class RegistryServer:
    def __init__(self, *, npm: dict[str, Any] | None, pypi: dict[str, Any] | None):
        routes = {
            "/%40gigaloom%2Fweb": npm,
            "/pypi/gigaloom/json": pypi,
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = routes.get(self.path)
                if payload is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                content = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, format, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


@pytest.mark.parametrize(
    ("mode", "npm_exists", "pypi_exists"),
    [
        ("initial", False, False),
        ("recover-pypi", True, False),
        ("recover-npm", False, True),
        ("release-assets-only", True, True),
    ],
)
def test_registry_guard_accepts_only_the_requested_exact_state(
    candidate: dict[str, Any],
    mode: str,
    npm_exists: bool,
    pypi_exists: bool,
):
    module = load_module()
    npm = npm_payload(candidate, exact=True) if npm_exists else None
    pypi = pypi_payload(candidate, exact=True) if pypi_exists else None
    with RegistryServer(npm=npm, pypi=pypi) as base_url:
        result = module.inspect_registries(
            release_path=candidate["release"],
            artifact_dir=candidate["artifact_dir"],
            mode=mode,
            pypi_base_url=base_url,
            npm_base_url=base_url,
        )

    assert result["mode"] == mode
    assert result["npm"] == ("exact" if npm_exists else "absent")
    assert result["pypi"] == ("exact" if pypi_exists else "absent")


def test_registry_guard_rejects_wrong_recovery_mode(candidate: dict[str, Any]):
    module = load_module()
    with RegistryServer(npm=npm_payload(candidate, exact=True), pypi=None) as base_url:
        with pytest.raises(module.RegistryGuardError, match="does not permit"):
            module.inspect_registries(
                release_path=candidate["release"],
                artifact_dir=candidate["artifact_dir"],
                mode="initial",
                pypi_base_url=base_url,
                npm_base_url=base_url,
            )


@pytest.mark.parametrize("registry", ["npm", "pypi"])
def test_registry_guard_rejects_existing_non_candidate_bytes(
    candidate: dict[str, Any],
    registry: str,
):
    module = load_module()
    npm = npm_payload(candidate, exact=registry != "npm")
    pypi = pypi_payload(candidate, exact=registry != "pypi")
    with RegistryServer(npm=npm, pypi=pypi) as base_url:
        with pytest.raises(module.RegistryGuardError, match="non-candidate"):
            module.inspect_registries(
                release_path=candidate["release"],
                artifact_dir=candidate["artifact_dir"],
                mode="release-assets-only",
                pypi_base_url=base_url,
                npm_base_url=base_url,
            )
