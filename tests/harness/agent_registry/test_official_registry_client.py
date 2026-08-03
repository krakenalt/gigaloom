"""Official ACP Registry client, cache, and immutable-index contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pytest

from gigaloom.contracts import ACPDistributionKind
from gigaloom.harnesses.agent_profiles.registry import (
    MAX_REGISTRY_DOCUMENT_BYTES,
    OFFICIAL_ACP_REGISTRY_URL,
    ACPRegistryCache,
    ACPRegistryIndex,
    OfficialACPRegistryClient,
    RegistryCacheError,
    RegistryFetchRequest,
    RegistryFetchResponse,
    RegistryNetworkError,
    RegistrySchemaError,
    RegistryUnavailableError,
    decode_registry_document,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "acp_registry"


def _fixture_bytes() -> bytes:
    return (FIXTURES / "registry-v1-valid.json").read_bytes()


def _fixture_object() -> dict[str, Any]:
    return json.loads(_fixture_bytes())


def _encoded(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def test_decoder_projects_all_distribution_families_without_execution() -> None:
    catalog = decode_registry_document(
        _fixture_bytes(),
        fetched_at=NOW,
        etag='"fixture-etag"',
        last_modified="Sat, 01 Aug 2026 09:00:00 GMT",
    )

    assert catalog.registry_version == "1.0.0"
    assert catalog.snapshot.source_url == OFFICIAL_ACP_REGISTRY_URL
    assert catalog.snapshot.entry_count == 6
    assert catalog.snapshot.stale is False
    assert catalog.offline is False
    assert tuple(entry.registry_id for entry in catalog.entries) == (
        "binary-unverified",
        "binary-verified",
        "multi-distribution",
        "npx-exact",
        "platform-miss",
        "uvx-exact",
    )

    by_id = {entry.registry_id: entry for entry in catalog.entries}
    assert {
        distribution.kind for distribution in by_id["multi-distribution"].distributions
    } == {ACPDistributionKind.BINARY, ACPDistributionKind.NPX}
    verified = by_id["binary-verified"].distributions[0]
    assert verified.platform == "darwin"
    assert verified.architecture == "aarch64"
    assert verified.command == "bin/binary-verified"
    assert verified.expected_integrity == (
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )
    assert by_id["binary-unverified"].distributions[0].expected_integrity is None
    assert by_id["npx-exact"].distributions[0].package_or_archive == (
        "@example/npx-agent@1.2.3"
    )
    assert by_id["uvx-exact"].distributions[0].package_or_archive == (
        "uvx-agent==2.3.4"
    )
    assert all(
        entry.snapshot_digest == catalog.snapshot.snapshot_digest
        for entry in catalog.entries
    )


def test_decoder_accepts_only_the_current_empty_extensions_projection() -> None:
    payload = _fixture_object()
    payload["extensions"] = []
    payload["agents"][0]["version"] = "2026.07.23"
    payload["agents"][0]["license"] = "Apache 2.0"
    payload["agents"][0]["distribution"]["binary"]["windows-x86_64"] = {
        "archive": "https://downloads.example.com/agent-windows.zip",
        "cmd": ".\\bin\\agent.exe",
    }

    catalog = decode_registry_document(_encoded(payload), fetched_at=NOW)

    assert catalog.snapshot.entry_count == 6
    entry = next(item for item in catalog.entries if item.version == "2026.07.23")
    assert entry.license == "Apache 2.0"
    windows = next(item for item in entry.distributions if item.platform == "windows")
    assert windows.command == "bin/agent.exe"


@pytest.mark.parametrize("extensions", [[{"id": "unknown"}], {}])
def test_decoder_rejects_unsupported_registry_extensions(extensions: object) -> None:
    payload = _fixture_object()
    payload["extensions"] = extensions

    with pytest.raises(RegistrySchemaError, match="registry extensions"):
        decode_registry_document(_encoded(payload), fetched_at=NOW)


@pytest.mark.parametrize("version", ["latest", "1", "1.2", "1.2.3.4"])
def test_decoder_rejects_non_registry_versions(version: str) -> None:
    payload = _fixture_object()
    payload["agents"][0]["version"] = version

    with pytest.raises(RegistrySchemaError, match="version is not registry-compatible"):
        decode_registry_document(_encoded(payload), fetched_at=NOW)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value.update({"schema": "unexpected"}),
            "unknown fields",
        ),
        (
            lambda value: value.update({"version": "2.0.0"}),
            "unsupported registry schema version",
        ),
        (
            lambda value: value["agents"].append(value["agents"][0]),
            "duplicate registry id",
        ),
        (
            lambda value: value["agents"][0].update({"install": True}),
            "unknown fields",
        ),
        (
            lambda value: value["agents"][0]["distribution"]["binary"][
                "darwin-aarch64"
            ].update({"cmd": "../escape"}),
            "command",
        ),
        (
            lambda value: value["agents"][0]["distribution"]["binary"][
                "darwin-aarch64"
            ].update({"archive": "http://downloads.example.com/agent.zip"}),
            "HTTPS URL",
        ),
        (
            lambda value: value["agents"][0]["distribution"]["binary"][
                "darwin-aarch64"
            ].update({"env": {"API_TOKEN": "not-admitted"}}),
            "secret-bearing key",
        ),
        (
            lambda value: value["agents"][0]["distribution"].update(
                {"container": {"image": "example.invalid/agent"}}
            ),
            "unknown fields",
        ),
        (
            lambda value: value["agents"][0]["distribution"]["binary"].update(
                {
                    "freebsd-x86_64": {
                        "archive": "https://downloads.example.com/agent.zip",
                        "cmd": "agent",
                    }
                }
            ),
            "unknown fields",
        ),
    ],
)
def test_decoder_rejects_schema_drift_and_unsafe_metadata(
    mutate: Any,
    message: str,
) -> None:
    payload = _fixture_object()
    mutate(payload)

    with pytest.raises(RegistrySchemaError, match=message):
        decode_registry_document(_encoded(payload), fetched_at=NOW)


def test_decoder_rejects_duplicate_json_keys_and_oversized_documents() -> None:
    duplicate_keys = b'{"version":"1.0.0","version":"1.0.0","agents":[]}'
    with pytest.raises(RegistrySchemaError, match="duplicate JSON key"):
        decode_registry_document(duplicate_keys, fetched_at=NOW)

    with pytest.raises(RegistrySchemaError, match="too large"):
        decode_registry_document(
            b" " * (MAX_REGISTRY_DOCUMENT_BYTES + 1),
            fetched_at=NOW,
        )


def test_cache_is_content_addressed_stale_aware_and_tamper_evident(
    tmp_path: Path,
) -> None:
    cache = ACPRegistryCache(
        tmp_path / "registry-cache-v1",
        stale_after=timedelta(hours=1),
    )
    payload = _fixture_bytes()
    catalog = decode_registry_document(payload, fetched_at=NOW)
    cache.store(payload, catalog)

    loaded = cache.load(now=NOW + timedelta(minutes=30))
    assert loaded is not None
    assert loaded.from_cache is True
    assert loaded.snapshot.stale is False
    stale = cache.load(now=NOW + timedelta(hours=2))
    assert stale is not None
    assert stale.snapshot.stale is True

    blob = (
        tmp_path
        / "registry-cache-v1"
        / "snapshots"
        / f"{catalog.snapshot.snapshot_digest}.json"
    )
    blob.write_bytes(b"{}")
    with pytest.raises(RegistryCacheError, match="digest mismatch"):
        cache.load(now=NOW)


def test_cache_rejects_symlinked_state_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(RegistryCacheError, match="non-symlink directory"):
        ACPRegistryCache(linked).load(now=NOW)


def test_immutable_index_keeps_search_local_bounded_and_deterministic() -> None:
    catalog = decode_registry_document(_fixture_bytes(), fetched_at=NOW)
    index = ACPRegistryIndex.build(catalog.entries)

    assert index.get("npx-exact").name == "Exact Npx Agent"
    assert tuple(item.registry_id for item in index.search("npx")) == (
        "multi-distribution",
        "npx-exact",
    )
    assert tuple(item.registry_id for item in index.search("distribution")) == (
        "multi-distribution",
    )
    assert len(index.search("", limit=3)) == 3
    assert index.search("agent", limit=2) == tuple(
        sorted(index.search("agent", limit=2), key=lambda item: item.registry_id)
    )
    with pytest.raises(TypeError):
        index.entries_by_id["new"] = catalog.entries[0]  # type: ignore[index]


@dataclass
class _FakeTransport:
    outcomes: list[RegistryFetchResponse | Exception]

    def __post_init__(self) -> None:
        self.requests: list[RegistryFetchRequest] = []

    def fetch(self, request: RegistryFetchRequest) -> RegistryFetchResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(
    *,
    status_code: int = 200,
    body: bytes | None = None,
    headers: Iterable[tuple[str, str]] = (),
) -> RegistryFetchResponse:
    return RegistryFetchResponse(
        status_code=status_code,
        final_url=OFFICIAL_ACP_REGISTRY_URL,
        headers=tuple(headers),
        body=_fixture_bytes() if body is None else body,
    )


def test_client_uses_conditional_refresh_and_304_without_redecoding_network(
    tmp_path: Path,
) -> None:
    transport = _FakeTransport(
        [
            _response(
                headers=(
                    ("ETag", '"fixture-etag"'),
                    ("Last-Modified", "Sat, 01 Aug 2026 09:00:00 GMT"),
                    ("Content-Type", "application/json"),
                )
            ),
            _response(status_code=304, body=b""),
        ]
    )
    clock = iter((NOW, NOW + timedelta(minutes=5)))
    client = OfficialACPRegistryClient(
        cache=ACPRegistryCache(tmp_path / "cache"),
        transport=transport,
        now=lambda: next(clock),
    )

    first = client.catalog(refresh=True)
    second = client.catalog(refresh=True)

    assert first.snapshot.snapshot_digest == second.snapshot.snapshot_digest
    assert second.from_cache is True
    assert second.snapshot.stale is False
    assert transport.requests[1].headers["If-None-Match"] == '"fixture-etag"'
    assert transport.requests[1].headers["If-Modified-Since"] == (
        "Sat, 01 Aug 2026 09:00:00 GMT"
    )


def test_network_outage_uses_valid_cache_honestly_and_fails_without_one(
    tmp_path: Path,
) -> None:
    populated_cache = ACPRegistryCache(tmp_path / "populated")
    payload = _fixture_bytes()
    populated_cache.store(payload, decode_registry_document(payload, fetched_at=NOW))
    offline_client = OfficialACPRegistryClient(
        cache=populated_cache,
        transport=_FakeTransport([RegistryNetworkError("offline")]),
        now=lambda: NOW + timedelta(minutes=5),
    )

    fallback = offline_client.catalog(refresh=True)
    assert fallback.offline is True
    assert fallback.from_cache is True
    assert fallback.snapshot.stale is True
    assert fallback.refresh_error_code == "network_unavailable"

    empty_client = OfficialACPRegistryClient(
        cache=ACPRegistryCache(tmp_path / "empty"),
        transport=_FakeTransport([RegistryNetworkError("offline")]),
        now=lambda: NOW,
    )
    with pytest.raises(RegistryUnavailableError, match="no valid cached snapshot"):
        empty_client.catalog(refresh=True)


def test_client_construction_and_cache_miss_never_fetch_implicitly(
    tmp_path: Path,
) -> None:
    transport = _FakeTransport([_response()])
    client = OfficialACPRegistryClient(
        cache=ACPRegistryCache(tmp_path / "cache"),
        transport=transport,
        now=lambda: NOW,
    )

    assert transport.requests == []
    with pytest.raises(RegistryUnavailableError, match="refresh is required"):
        client.catalog()
    assert transport.requests == []


def test_search_never_refreshes_or_executes_and_invalid_refresh_keeps_cache(
    tmp_path: Path,
) -> None:
    invalid = _fixture_object()
    invalid["unknown"] = True
    transport = _FakeTransport(
        [
            _response(headers=(("Content-Type", "application/json"),)),
            _response(
                body=_encoded(invalid),
                headers=(("Content-Type", "application/json"),),
            ),
        ]
    )
    client = OfficialACPRegistryClient(
        cache=ACPRegistryCache(tmp_path / "cache"),
        transport=transport,
        now=lambda: NOW,
    )
    original = client.catalog(refresh=True)
    request_count = len(transport.requests)

    assert tuple(item.registry_id for item in client.search("uvx")) == ("uvx-exact",)
    assert len(transport.requests) == request_count
    with pytest.raises(RegistrySchemaError, match="unknown fields"):
        client.catalog(refresh=True)
    cached = client.catalog()
    assert cached.snapshot.snapshot_digest == original.snapshot.snapshot_digest
