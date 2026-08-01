"""Immutable local search index for one validated ACP Registry revision."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping, Sequence

from gigaloom.contracts import ACPRegistryEntryV1


MAX_REGISTRY_SEARCH_RESULTS = 100
MAX_REGISTRY_SEARCH_QUERY_CHARS = 256
MAX_REGISTRY_SEARCH_TOKENS_PER_ENTRY = 64
MAX_REGISTRY_SEARCH_TOKEN_CHARS = 64
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ACPRegistryIndex:
    """O(1) exact lookup plus precomputed prefix search, with no network owner."""

    entries: tuple[ACPRegistryEntryV1, ...]
    entries_by_id: Mapping[str, ACPRegistryEntryV1]
    _ids_by_prefix: Mapping[str, tuple[str, ...]]

    @classmethod
    def build(cls, entries: Sequence[ACPRegistryEntryV1]) -> ACPRegistryIndex:
        """Build an immutable index once for a decoded snapshot."""
        ordered = tuple(sorted(entries, key=lambda entry: entry.registry_id))
        if len(ordered) > 1_000 or any(
            not isinstance(entry, ACPRegistryEntryV1) for entry in ordered
        ):
            raise ValueError("ACP registry index entries are invalid or too large")
        by_id = {entry.registry_id: entry for entry in ordered}
        if len(by_id) != len(ordered):
            raise ValueError("ACP registry index ids must be unique")
        prefixes: dict[str, set[str]] = {}
        for entry in ordered:
            searchable = " ".join(
                (
                    entry.registry_id,
                    entry.name,
                    entry.description,
                    entry.license,
                    *entry.authors,
                    *(item.kind.value for item in entry.distributions),
                )
            ).lower()
            tokens = sorted(set(_TOKEN_RE.findall(searchable)))[
                :MAX_REGISTRY_SEARCH_TOKENS_PER_ENTRY
            ]
            for token in tokens:
                token = token[:MAX_REGISTRY_SEARCH_TOKEN_CHARS]
                for length in range(1, len(token) + 1):
                    prefixes.setdefault(token[:length], set()).add(entry.registry_id)
        frozen_prefixes = {
            prefix: tuple(sorted(ids)) for prefix, ids in prefixes.items()
        }
        return cls(
            entries=ordered,
            entries_by_id=MappingProxyType(by_id),
            _ids_by_prefix=MappingProxyType(frozen_prefixes),
        )

    def get(self, registry_id: str) -> ACPRegistryEntryV1:
        """Return one exact registry entry in constant time."""
        try:
            return self.entries_by_id[registry_id]
        except KeyError as error:
            raise KeyError(f"unknown ACP registry id: {registry_id}") from error

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> tuple[ACPRegistryEntryV1, ...]:
        """Search precomputed local prefixes without fetching or parsing metadata."""
        if not isinstance(query, str) or len(query) > MAX_REGISTRY_SEARCH_QUERY_CHARS:
            raise ValueError("ACP registry search query is invalid")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_REGISTRY_SEARCH_RESULTS
        ):
            raise ValueError("ACP registry search limit is invalid")
        tokens = tuple(_TOKEN_RE.findall(query.lower()))
        if any(len(token) > MAX_REGISTRY_SEARCH_TOKEN_CHARS for token in tokens):
            raise ValueError("ACP registry search token is too long")
        if not tokens:
            return self.entries[:limit]
        candidate_ids: set[str] | None = None
        for token in tokens:
            token_ids = set(self._ids_by_prefix.get(token, ()))
            candidate_ids = (
                token_ids
                if candidate_ids is None
                else candidate_ids.intersection(token_ids)
            )
            if not candidate_ids:
                return ()
        assert candidate_ids is not None
        return tuple(self.entries_by_id[item] for item in sorted(candidate_ids))[:limit]
