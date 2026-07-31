#!/usr/bin/env python3
"""Fail closed unless registries match one immutable release candidate."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


PYPI_BASE_URL = "https://pypi.org"
NPM_BASE_URL = "https://registry.npmjs.org"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
EXPECTED_MANIFEST_FIELDS = {
    "git_tag",
    "npm_package",
    "npm_version",
    "python_distribution",
    "python_version",
    "release",
}
EXPECTED_STATES = {
    "initial": {"npm": "absent", "pypi": "absent"},
    "recover-npm": {"npm": "absent", "pypi": "exact"},
    "recover-pypi": {"npm": "exact", "pypi": "absent"},
    "release-assets-only": {"npm": "exact", "pypi": "exact"},
}


class RegistryGuardError(RuntimeError):
    """Raised when registry state is unsafe for the requested operation."""


def _json_object(content: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RegistryGuardError(f"{label} response is malformed") from exc
    if not isinstance(payload, Mapping):
        raise RegistryGuardError(f"{label} response must be a JSON object")
    return payload


def _release_identity(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RegistryGuardError("release manifest is missing or malformed") from exc
    if (
        not isinstance(payload, Mapping)
        or set(payload) != EXPECTED_MANIFEST_FIELDS
        or any(
            not isinstance(payload[field], str) for field in EXPECTED_MANIFEST_FIELDS
        )
    ):
        raise RegistryGuardError("release manifest does not match schema v1")
    identity = {field: payload[field] for field in EXPECTED_MANIFEST_FIELDS}
    if (
        identity["python_distribution"] != "gigaloom"
        or identity["npm_package"] != "@gigaloom/web"
        or identity["git_tag"] != f"v{identity['release']}"
        or identity["npm_version"] != identity["release"]
    ):
        raise RegistryGuardError("release manifest identity is inconsistent")
    return identity


def _single_artifact(root: Path, pattern: str, *, label: str) -> Path:
    matches = sorted(path for path in root.glob(pattern) if path.is_file())
    if len(matches) != 1 or matches[0].is_symlink():
        raise RegistryGuardError(
            f"expected exactly one regular {label} matching {pattern!r}"
        )
    return matches[0]


def _candidate_artifacts(
    identity: Mapping[str, str],
    artifact_dir: Path,
) -> dict[str, Path]:
    if not artifact_dir.is_dir() or artifact_dir.is_symlink():
        raise RegistryGuardError("candidate artifact directory is unavailable")
    python_base = identity["python_distribution"].replace("-", "_")
    npm_base = identity["npm_package"].removeprefix("@").replace("/", "-")
    return {
        "wheel": _single_artifact(
            artifact_dir,
            f"{python_base}-{identity['python_version']}-*.whl",
            label="wheel",
        ),
        "sdist": _single_artifact(
            artifact_dir,
            f"{python_base}-{identity['python_version']}.tar.gz",
            label="sdist",
        ),
        "npm": _single_artifact(
            artifact_dir,
            f"{npm_base}-{identity['npm_version']}.tgz",
            label="npm tarball",
        ),
    }


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RegistryGuardError(
            f"candidate artifact is unreadable: {path.name}"
        ) from exc
    return digest.hexdigest()


def _get_json(url: str, *, label: str, timeout: float) -> Mapping[str, Any] | None:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RegistryGuardError(f"{label} registry returned HTTP {exc.code}") from exc
    except (OSError, URLError) as exc:
        raise RegistryGuardError(f"{label} registry is unavailable") from exc
    if len(content) > MAX_RESPONSE_BYTES:
        raise RegistryGuardError(f"{label} registry response is too large")
    return _json_object(content, label=label)


def _pypi_state(
    *,
    identity: Mapping[str, str],
    artifacts: Mapping[str, Path],
    base_url: str,
    timeout: float,
) -> str:
    url = (
        f"{base_url.rstrip('/')}/pypi/"
        f"{quote(identity['python_distribution'], safe='')}/json"
    )
    payload = _get_json(url, label="PyPI", timeout=timeout)
    if payload is None:
        return "absent"
    releases = payload.get("releases")
    if not isinstance(releases, Mapping):
        raise RegistryGuardError("PyPI response has no releases object")
    files = releases.get(identity["python_version"])
    if files is None:
        return "absent"
    if not isinstance(files, list):
        raise RegistryGuardError("PyPI release files must be a list")

    expected = {
        artifacts["wheel"].name: _digest(artifacts["wheel"], "sha256"),
        artifacts["sdist"].name: _digest(artifacts["sdist"], "sha256"),
    }
    actual: dict[str, str] = {}
    for item in files:
        if not isinstance(item, Mapping):
            raise RegistryGuardError("PyPI release file is malformed")
        filename = item.get("filename")
        digests = item.get("digests")
        if (
            not isinstance(filename, str)
            or not isinstance(digests, Mapping)
            or not isinstance(digests.get("sha256"), str)
            or filename in actual
        ):
            raise RegistryGuardError("PyPI release file digest is malformed")
        actual[filename] = digests["sha256"]
    if actual != expected:
        raise RegistryGuardError("PyPI version exists with non-candidate files")
    return "exact"


def _npm_state(
    *,
    identity: Mapping[str, str],
    artifact: Path,
    base_url: str,
    timeout: float,
) -> str:
    url = f"{base_url.rstrip('/')}/{quote(identity['npm_package'], safe='')}"
    payload = _get_json(url, label="npm", timeout=timeout)
    if payload is None:
        return "absent"
    versions = payload.get("versions")
    if not isinstance(versions, Mapping):
        raise RegistryGuardError("npm response has no versions object")
    version = versions.get(identity["npm_version"])
    if version is None:
        return "absent"
    if not isinstance(version, Mapping) or not isinstance(version.get("dist"), Mapping):
        raise RegistryGuardError("npm version metadata is malformed")
    dist = version["dist"]
    try:
        sha512 = hashlib.sha512(artifact.read_bytes()).digest()
    except OSError as exc:
        raise RegistryGuardError(
            f"candidate artifact is unreadable: {artifact.name}"
        ) from exc
    expected = {
        "integrity": f"sha512-{base64.b64encode(sha512).decode('ascii')}",
        "shasum": _digest(artifact, "sha1"),
    }
    actual = {field: dist.get(field) for field in expected}
    if actual != expected:
        raise RegistryGuardError("npm version exists with non-candidate bytes")
    return "exact"


def inspect_registries(
    *,
    release_path: Path,
    artifact_dir: Path,
    mode: str,
    pypi_base_url: str = PYPI_BASE_URL,
    npm_base_url: str = NPM_BASE_URL,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Return verified registry state or raise on unsafe publication."""
    if mode not in EXPECTED_STATES:
        raise RegistryGuardError(f"unsupported recovery mode: {mode!r}")
    identity = _release_identity(release_path)
    artifacts = _candidate_artifacts(identity, artifact_dir)
    states = {
        "npm": _npm_state(
            identity=identity,
            artifact=artifacts["npm"],
            base_url=npm_base_url,
            timeout=timeout,
        ),
        "pypi": _pypi_state(
            identity=identity,
            artifacts=artifacts,
            base_url=pypi_base_url,
            timeout=timeout,
        ),
    }
    if states != EXPECTED_STATES[mode]:
        raise RegistryGuardError(
            f"registry state {states} does not permit recovery mode {mode!r}"
        )
    return {
        "mode": mode,
        "npm": states["npm"],
        "npm_package": identity["npm_package"],
        "npm_version": identity["npm_version"],
        "pypi": states["pypi"],
        "python_distribution": identity["python_distribution"],
        "python_version": identity["python_version"],
        "release": identity["release"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Validate registry state, retrying only for expected propagation delay."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=sorted(EXPECTED_STATES), required=True)
    parser.add_argument("--pypi-base-url", default=PYPI_BASE_URL)
    parser.add_argument("--npm-base-url", default=NPM_BASE_URL)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--retry-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    if args.attempts < 1 or args.retry_seconds < 0 or args.timeout <= 0:
        parser.error(
            "attempts and timeout must be positive; retry seconds cannot be negative"
        )

    last_error: RegistryGuardError | None = None
    for attempt in range(args.attempts):
        try:
            result = inspect_registries(
                release_path=args.release.resolve(),
                artifact_dir=args.artifact_dir.resolve(),
                mode=args.mode,
                pypi_base_url=args.pypi_base_url,
                npm_base_url=args.npm_base_url,
                timeout=args.timeout,
            )
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        except (OSError, ValueError, RegistryGuardError) as error:
            last_error = RegistryGuardError(str(error))
            if attempt + 1 < args.attempts:
                time.sleep(args.retry_seconds)
    assert last_error is not None
    parser.error(str(last_error))


if __name__ == "__main__":
    raise SystemExit(main())
