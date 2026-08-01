"""Bounded cache for fingerprint-exact compatibility observations."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from gigaloom.contracts.compatibility import (
    CompatibilityObservationV1,
)
from gigaloom.contracts.compatibility_fingerprints import (
    CompatibilityProbeCacheKeyV1,
)


class CompatibilityObservationCache:
    """Retain only fresh observations under their complete cache fingerprint."""

    def __init__(self, *, max_entries: int = 128) -> None:
        if max_entries <= 0 or max_entries > 4096:
            raise ValueError("compatibility cache size is invalid")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, CompatibilityObservationV1] = OrderedDict()

    def get(
        self,
        key: CompatibilityProbeCacheKeyV1,
        *,
        now: datetime,
    ) -> CompatibilityObservationV1 | None:
        """Return a fresh exact-key observation or invalidate stale evidence."""
        if not isinstance(key, CompatibilityProbeCacheKeyV1):
            raise ValueError("compatibility cache key is invalid")
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("compatibility cache time must be timezone-aware")
        observation = self._entries.get(key.digest)
        if observation is None:
            return None
        if observation.cache_key_digest != key.digest or observation.expires_at <= now:
            self._entries.pop(key.digest, None)
            return None
        self._entries.move_to_end(key.digest)
        return observation

    def put(
        self,
        key: CompatibilityProbeCacheKeyV1,
        observation: CompatibilityObservationV1,
    ) -> None:
        """Store one observation only under its bound complete fingerprint."""
        if not isinstance(key, CompatibilityProbeCacheKeyV1):
            raise ValueError("compatibility cache key is invalid")
        if not isinstance(observation, CompatibilityObservationV1):
            raise ValueError("compatibility cache observation is invalid")
        if observation.cache_key_digest != key.digest:
            raise ValueError("compatibility observation cache key mismatch")
        self._entries[key.digest] = observation
        self._entries.move_to_end(key.digest)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        """Invalidate every retained compatibility observation."""
        self._entries.clear()
