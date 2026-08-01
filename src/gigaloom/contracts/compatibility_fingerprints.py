"""Content-free compatibility cache fingerprints."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any


_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


@dataclass(frozen=True, slots=True)
class CompatibilityProbeCacheKeyV1:
    """All fingerprints that invalidate a compatibility observation."""

    executable_identity: str
    profile_digest: str
    command_tokens_digest: str
    protocol_handshake_digest: str
    platform: str
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        for value, label in (
            (self.executable_identity, "executable identity"),
            (self.profile_digest, "profile digest"),
            (self.command_tokens_digest, "command tokens digest"),
            (self.protocol_handshake_digest, "protocol handshake digest"),
        ):
            if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
                raise ValueError(f"{label} must be a lowercase sha256 digest")
        if (
            not isinstance(self.platform, str)
            or _IDENTITY_RE.fullmatch(self.platform) is None
        ):
            raise ValueError("platform is invalid")
        object.__setattr__(
            self,
            "digest",
            _canonical_digest(
                {
                    "executable_identity": self.executable_identity,
                    "profile_digest": self.profile_digest,
                    "command_tokens_digest": self.command_tokens_digest,
                    "protocol_handshake_digest": self.protocol_handshake_digest,
                    "platform": self.platform,
                }
            ),
        )


def digest_command_tokens(command: tuple[str, ...]) -> str:
    """Digest exact argv tokens without retaining them in observations."""
    if not command or any(
        not isinstance(token, str) or "\0" in token for token in command
    ):
        return _canonical_digest({"command": []})
    return _canonical_digest({"command": list(command)})


def _canonical_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
