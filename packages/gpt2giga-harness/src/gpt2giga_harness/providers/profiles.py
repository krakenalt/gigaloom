"""Provider-neutral profile contracts and pre-spawn compatibility admission."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib.metadata import entry_points
import re
from typing import Iterable

from gpt2giga_harness.execution import (
    ExecutionTransport,
    ProviderRef,
    RouteRef,
    SnapshotEvidenceRef,
)
from gpt2giga_harness.registries import (
    EntryPointFamily,
    RegistrationOutcome,
    RegistryCollisionError,
    VersionedRegistryKernel,
)
from gpt2giga_harness.secrets import (
    SecretReference,
    SecretReferenceKind,
)
from gpt2giga_harness.types import redact_secrets


PROVIDER_PROFILE_SCHEMA_VERSION = 1
ROUTE_PROFILE_SCHEMA_VERSION = 1
PROVIDER_COMPATIBILITY_SCHEMA_VERSION = 1
NEUTRAL_PROVIDER_ENTRY_POINT_GROUP = "agent_workbench.provider_adapters.v1"
PROVIDER_ADAPTER_ENTRY_POINTS = EntryPointFamily(
    registry_id="provider_adapter",
    api_version=1,
    primary_group=NEUTRAL_PROVIDER_ENTRY_POINT_GROUP,
)
MAX_DISCOVERY_ERRORS = 20
MAX_DISCOVERY_ERROR_CHARS = 400
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


class ProviderProtocol(str, Enum):
    """Protocol family spoken by one provider endpoint."""

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC_COMPATIBLE = "anthropic_compatible"
    GEMINI_COMPATIBLE = "gemini_compatible"


class ModelPurpose(str, Enum):
    """Independent purpose assigned to one model route."""

    CODING = "coding"
    TITLE = "title"
    EVALUATION = "evaluation"
    FALLBACK = "fallback"


class AuthenticationOwnership(str, Enum):
    """Owner that supplies authentication at execution time."""

    SECRET_REFERENCE = "secret_reference"
    PROVIDER_NATIVE = "provider_native"
    NONE = "none"


class ProviderOwnership(str, Enum):
    """Configuration source that owns a provider profile."""

    BUILT_IN = "built_in"
    USER = "user"
    PROJECT = "project"
    ENVIRONMENT = "environment"
    MANAGED_POLICY = "managed_policy"
    MIGRATED_LEGACY = "migrated_legacy"


@dataclass(frozen=True, order=True)
class ModelPurposeDefault:
    """Default model selected for one independent route purpose."""

    purpose: ModelPurpose
    model: str

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, ModelPurpose):
            raise ValueError("model default purpose is invalid")
        _validate_text(self.model, field_name="default model")


@dataclass(frozen=True)
class ProviderAuthentication:
    """Reference-only provider authentication configuration."""

    ownership: AuthenticationOwnership
    secret_reference: SecretReference | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, AuthenticationOwnership):
            raise ValueError("authentication ownership is invalid")
        if self.ownership is AuthenticationOwnership.SECRET_REFERENCE:
            if not isinstance(self.secret_reference, SecretReference):
                raise ValueError("secret_reference authentication requires SecretRef")
            if self.secret_reference.kind is SecretReferenceKind.TEST:
                raise ValueError("test SecretRef cannot be persisted in a provider")
        elif self.secret_reference is not None:
            raise ValueError(
                "provider-native or unauthenticated profiles cannot retain SecretRef"
            )


@dataclass(frozen=True)
class ProviderProfile:
    """Strict persisted model-provider configuration without adapter claims."""

    id: str
    revision: str
    display_name: str
    protocol: ProviderProtocol
    dialect: str
    base_url: str
    route_prefix: str | None
    authentication: ProviderAuthentication
    ownership: ProviderOwnership
    capability_evidence: tuple[SnapshotEvidenceRef, ...] = ()
    default_models: tuple[ModelPurposeDefault, ...] = ()
    tls_policy_ref: str | None = None
    proxy_policy_ref: str | None = None
    egress_policy_ref: str | None = None
    offline: bool = False
    discovery_strategy: str = "none"
    discovery_cache_ttl_seconds: int = 0
    schema_version: int = PROVIDER_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported provider profile schema_version")
        _validate_identity(self.id, field_name="provider id")
        _validate_identity(self.revision, field_name="provider revision")
        _validate_text(self.display_name, field_name="provider display_name")
        if not isinstance(self.protocol, ProviderProtocol):
            raise ValueError("provider protocol is invalid")
        _validate_identity(self.dialect, field_name="provider dialect")
        object.__setattr__(self, "base_url", _canonical_base_url(self.base_url))
        object.__setattr__(
            self,
            "route_prefix",
            _canonical_route_prefix(self.route_prefix),
        )
        if not isinstance(self.authentication, ProviderAuthentication):
            raise ValueError("provider authentication is invalid")
        if not isinstance(self.ownership, ProviderOwnership):
            raise ValueError("provider ownership is invalid")
        object.__setattr__(
            self,
            "capability_evidence",
            _normalize_evidence(self.capability_evidence),
        )
        object.__setattr__(
            self,
            "default_models",
            _normalize_model_defaults(self.default_models),
        )
        for field_name in (
            "tls_policy_ref",
            "proxy_policy_ref",
            "egress_policy_ref",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_identity(value, field_name=field_name)
        if not isinstance(self.offline, bool):
            raise ValueError("provider offline must be a boolean")
        _validate_identity(
            self.discovery_strategy,
            field_name="provider discovery_strategy",
        )
        _validate_non_negative_int(
            self.discovery_cache_ttl_seconds,
            field_name="provider discovery_cache_ttl_seconds",
        )

    @property
    def ref(self) -> ProviderRef:
        """Return the minimal immutable execution reference."""
        return ProviderRef(self.id, self.revision)

    @property
    def effective_base_url(self) -> str:
        """Return the endpoint after applying the reviewed route prefix."""
        if self.route_prefix is None:
            return self.base_url
        return f"{self.base_url.rstrip('/')}{self.route_prefix}"


@dataclass(frozen=True)
class RouteProfile:
    """Strict persisted model route, separate from execution composition."""

    id: str
    revision: str
    provider: ProviderRef
    protocol: ProviderProtocol
    dialect: str
    effective_base_url: str
    purpose: ModelPurpose
    model: str
    authentication_ownership: AuthenticationOwnership
    capability_evidence: tuple[SnapshotEvidenceRef, ...] = ()
    schema_version: int = ROUTE_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported route profile schema_version")
        _validate_identity(self.id, field_name="route id")
        _validate_identity(self.revision, field_name="route revision")
        if not isinstance(self.provider, ProviderRef):
            raise ValueError("route provider must be a ProviderRef")
        if not isinstance(self.protocol, ProviderProtocol):
            raise ValueError("route protocol is invalid")
        _validate_identity(self.dialect, field_name="route dialect")
        object.__setattr__(
            self,
            "effective_base_url",
            _canonical_base_url(self.effective_base_url),
        )
        if not isinstance(self.purpose, ModelPurpose):
            raise ValueError("route model purpose is invalid")
        _validate_text(self.model, field_name="route model")
        if not isinstance(self.authentication_ownership, AuthenticationOwnership):
            raise ValueError("route authentication ownership is invalid")
        object.__setattr__(
            self,
            "capability_evidence",
            _normalize_evidence(self.capability_evidence),
        )

    @property
    def ref(self) -> RouteRef:
        """Return the minimal immutable execution reference."""
        return RouteRef(self.id, self.revision, self.provider)


@dataclass(frozen=True)
class AdapterProtocolCompatibility:
    """Versioned evidence that one Harness adapter admits one protocol dialect."""

    id: str
    revision: str
    harness_id: str
    adapter_version: str
    protocol: ProviderProtocol
    dialects: tuple[str, ...]
    transports: tuple[ExecutionTransport, ...]
    capabilities: tuple[str, ...]
    native_auth: bool
    evidence: tuple[SnapshotEvidenceRef, ...]
    schema_version: int = PROVIDER_COMPATIBILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_COMPATIBILITY_SCHEMA_VERSION:
            raise ValueError("unsupported provider compatibility schema_version")
        for name in ("id", "revision", "harness_id", "adapter_version"):
            _validate_identity(getattr(self, name), field_name=name)
        if not isinstance(self.protocol, ProviderProtocol):
            raise ValueError("compatibility protocol is invalid")
        object.__setattr__(
            self,
            "dialects",
            _normalize_identities(self.dialects, field_name="compatibility dialect"),
        )
        raw_transports = tuple(self.transports)
        if not raw_transports or any(
            not isinstance(item, ExecutionTransport) for item in raw_transports
        ):
            raise ValueError("compatibility transports are invalid")
        transports = tuple(sorted(set(raw_transports), key=lambda item: item.value))
        object.__setattr__(self, "transports", transports)
        object.__setattr__(
            self,
            "capabilities",
            _normalize_identities(
                self.capabilities,
                field_name="compatibility capability",
            ),
        )
        if not isinstance(self.native_auth, bool):
            raise ValueError("compatibility native_auth must be a boolean")
        normalized_evidence = _normalize_evidence(self.evidence)
        if not normalized_evidence:
            raise ValueError("compatibility requires immutable evidence")
        object.__setattr__(self, "evidence", normalized_evidence)


@dataclass(frozen=True)
class RouteAdmission:
    """Content-free result of provider/route compatibility validation."""

    provider: ProviderRef
    route: RouteRef
    compatibility_id: str
    compatibility_revision: str
    evidence: tuple[SnapshotEvidenceRef, ...]


class RouteCompatibilityError(ValueError):
    """Fail-closed reason for a route rejected before adapter spawn."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProviderCompatibilityRegistry:
    """Discover adapter-to-provider evidence through the neutral registry kernel."""

    def __init__(self) -> None:
        self._kernel = VersionedRegistryKernel[AdapterProtocolCompatibility](
            PROVIDER_ADAPTER_ENTRY_POINTS
        )
        self.discovery_errors: list[str] = []

    def register(
        self,
        compatibility: AdapterProtocolCompatibility,
    ) -> RegistrationOutcome:
        """Register one runtime compatibility declaration."""
        return self._register(
            compatibility,
            identity=_compatibility_identity(compatibility),
            source=f"runtime:{compatibility.id}",
        )

    def _register(
        self,
        compatibility: AdapterProtocolCompatibility,
        *,
        identity: str,
        source: str,
        allow_equivalent_duplicate: bool = False,
    ) -> RegistrationOutcome:
        if not isinstance(compatibility, AdapterProtocolCompatibility):
            raise TypeError("provider entry point must return compatibility evidence")
        return self._kernel.register(
            item_id=compatibility.id,
            item=compatibility,
            identity=identity,
            source=source,
            allow_equivalent_duplicate=allow_equivalent_duplicate,
        )

    def list(self) -> tuple[AdapterProtocolCompatibility, ...]:
        """Return registered declarations in deterministic order."""
        return tuple(sorted(self._kernel.values(), key=lambda item: item.id))

    def load_entry_points(self) -> None:
        """Load third-party provider compatibility declarations."""
        try:
            all_entry_points = entry_points()
        except Exception as exc:  # pragma: no cover - defensive importlib path
            self._record_discovery_error(
                "Provider entry-point discovery failed: "
                f"{type(exc).__name__} (details omitted)."
            )
            return
        selected = sorted(
            _select_entry_points(
                all_entry_points,
                PROVIDER_ADAPTER_ENTRY_POINTS.primary_group,
            ),
            key=_entry_point_sort_key,
        )
        for entry_point in selected:
            entry_name = str(getattr(entry_point, "name", "<unnamed>"))
            source = (
                f"entry-point:{PROVIDER_ADAPTER_ENTRY_POINTS.primary_group}:"
                f"{entry_name}"
            )
            try:
                loaded = entry_point.load()
                compatibility = _load_entry_point_compatibility(loaded)
                self._register(
                    compatibility,
                    identity=_entry_point_identity(entry_point, loaded),
                    source=source,
                    allow_equivalent_duplicate=True,
                )
            except RegistryCollisionError as exc:
                self._record_discovery_error(
                    "Provider compatibility id collision for "
                    f"{exc.item_id!r}: keeping {exc.existing_source}; "
                    f"rejected {exc.incoming_source}."
                )
            except Exception as exc:  # pragma: no cover - plugin failure path
                self._record_discovery_error(
                    f"{source}: {type(exc).__name__} (details omitted)."
                )

    def admit(
        self,
        provider: ProviderProfile,
        route: RouteProfile,
        *,
        harness_id: str,
        adapter_version: str,
        transport: ExecutionTransport,
        required_capabilities: Iterable[str] = (),
    ) -> RouteAdmission:
        """Validate every compatibility axis before an adapter process can spawn."""
        _validate_profile_route(provider, route)
        _validate_identity(harness_id, field_name="harness id")
        _validate_identity(adapter_version, field_name="adapter version")
        if not isinstance(transport, ExecutionTransport):
            raise RouteCompatibilityError(
                "transport_invalid",
                "execution transport is invalid",
            )
        required = set(
            _normalize_identities(
                required_capabilities,
                field_name="required capability",
                allow_empty=True,
            )
        )
        candidates = [
            item
            for item in self._kernel.values()
            if item.harness_id == harness_id
            and item.adapter_version == adapter_version
            and item.protocol is provider.protocol
            and provider.dialect in item.dialects
        ]
        if not candidates:
            raise RouteCompatibilityError(
                "adapter_protocol_incompatible",
                "Harness adapter has no evidence for the selected protocol dialect",
            )
        transport_candidates = [
            item for item in candidates if transport in item.transports
        ]
        if not transport_candidates:
            raise RouteCompatibilityError(
                "transport_incompatible",
                "Harness adapter does not admit the selected transport",
            )
        if provider.authentication.ownership is AuthenticationOwnership.PROVIDER_NATIVE:
            transport_candidates = [
                item for item in transport_candidates if item.native_auth
            ]
            if not transport_candidates:
                raise RouteCompatibilityError(
                    "native_auth_incompatible",
                    "Harness adapter has no native-auth evidence for this route",
                )
        provider_capabilities = _supported_capabilities(provider.capability_evidence)
        route_capabilities = _supported_capabilities(route.capability_evidence)
        if route.capability_evidence:
            provider_capabilities &= route_capabilities
        capable = [
            item
            for item in transport_candidates
            if required <= provider_capabilities and required <= set(item.capabilities)
        ]
        if not capable:
            raise RouteCompatibilityError(
                "capability_incompatible",
                "selected provider route lacks required capability evidence",
            )
        selected = sorted(capable, key=lambda item: item.id)[0]
        return RouteAdmission(
            provider=provider.ref,
            route=route.ref,
            compatibility_id=selected.id,
            compatibility_revision=selected.revision,
            evidence=selected.evidence,
        )

    @classmethod
    def with_builtins(cls) -> "ProviderCompatibilityRegistry":
        """Create a registry with conservative legacy compatibility evidence."""
        registry = cls()
        from gpt2giga_harness.providers.protocols.anthropic.compatible import (
            claude_code_anthropic_api_compatibility,
            claude_code_anthropic_cloud_compatibility,
        )
        from gpt2giga_harness.providers.protocols.openai.compatible import (
            codex_openai_compatibility,
            direct_chat_openai_compatibility,
        )
        from gpt2giga_harness.providers.protocols.gemini.compatible import (
            gemini_cli_api_compatibility,
            gemini_cli_vertex_compatibility,
        )

        factories = (
            *_BUILTIN_COMPATIBILITY_FACTORIES,
            direct_chat_openai_compatibility,
            codex_openai_compatibility,
            claude_code_anthropic_api_compatibility,
            claude_code_anthropic_cloud_compatibility,
            gemini_cli_api_compatibility,
            gemini_cli_vertex_compatibility,
        )
        for factory in factories:
            registry._register(
                factory(),
                identity=_implementation_identity(factory),
                source=f"built-in:{factory.__name__}",
            )
        return registry

    def _record_discovery_error(self, message: str) -> None:
        if len(self.discovery_errors) >= MAX_DISCOVERY_ERRORS:
            return
        safe_message = str(redact_secrets(message))
        self.discovery_errors.append(safe_message[:MAX_DISCOVERY_ERROR_CHARS])


from .profile_builtins import (  # noqa: E402, F401
    _BUILTIN_COMPATIBILITY_FACTORIES,
    _canonical_base_url,
    _canonical_route_prefix,
    _compatibility_identity,
    _entry_point_identity,
    _entry_point_sort_key,
    _implementation_identity,
    _load_entry_point_compatibility,
    _normalize_evidence,
    _normalize_identities,
    _normalize_model_defaults,
    _select_entry_points,
    _supported_capabilities,
    _validate_identity,
    _validate_non_negative_int,
    _validate_profile_route,
    _validate_text,
    claude_legacy_compatibility,
    codex_legacy_compatibility,
    direct_chat_legacy_compatibility,
    gemini_legacy_compatibility,
)
from .profile_serialization import (  # noqa: E402, F401
    migrate_legacy_provider_route,
    provider_profile_from_dict,
    provider_profile_to_dict,
    route_profile_from_dict,
    route_profile_to_dict,
)
