"""Explicit compatibility boundary for legacy full session bundle responses."""

from __future__ import annotations

from dataclasses import dataclass

from gpt2giga_harness.sessions import HarnessSessionStore
from gpt2giga_harness.sessions.models import HarnessSessionBundle
from gpt2giga_harness.ui.async_execution import AsyncExecutionDiagnostics


LEGACY_FULL_BUNDLE_MARKER = "legacy_full_session_bundle"


@dataclass(frozen=True, slots=True)
class LegacyFullBundleCompatibility:
    """Serve full bundles only for APIs whose public response requires them."""

    store: HarnessSessionStore
    diagnostics: AsyncExecutionDiagnostics

    def export_session_bundle(self, session_id: str) -> HarnessSessionBundle:
        """Return one explicit full export and mark the compatibility path."""
        bundle = self.store.export_session_bundle(session_id)
        self.diagnostics.record_marker(LEGACY_FULL_BUNDLE_MARKER)
        return bundle
