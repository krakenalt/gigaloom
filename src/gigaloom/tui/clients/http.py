"""HTTP attach TUI workbench transport adapter."""

from __future__ import annotations

from http.cookiejar import CookieJar
import json
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import (
    HTTPCookieProcessor,
    Request,
    build_opener,
)

import anyio


from gigaloom.tui.contracts import (
    MAX_SESSIONS,
    MAX_RESPONSE_BYTES,
    HTTP_TIMEOUT_SECONDS,
    WorkbenchClientError,
    SessionSummary,
    SessionActionBinding,
    SessionPreview,
    SessionExport,
    EnvironmentSummary,
    NavigationSnapshot,
)

from gigaloom.tui.projections.values import (
    _bounded_non_negative_int,
    _mapping,
    _mapping_items,
    _required_text,
    _optional_text,
    _path_identity,
)

from gigaloom.tui.projections.navigation import (
    _integration_summary_from_mapping,
    _selected_summary_id,
    _project_summary_from_mapping,
    _session_summary_from_mapping,
    _session_binding_payload,
    _session_preview_from_mapping,
    _harness_summary,
    _readiness_summary,
)

from gigaloom.tui.projections.environment import (
    _environment_summary_from_mapping,
)


from gigaloom.tui.clients.http_actions import _AttachedActionsMixin
from gigaloom.tui.clients.http_runs import _AttachedRunsMixin


def _normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("attach URL must be an HTTP(S) origin without credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


class AttachedWorkbenchClient(_AttachedActionsMixin, _AttachedRunsMixin):
    """Use the existing local REST contract without reading server storage."""

    transport_mode = "attach"

    def __init__(
        self,
        base_url: str,
        *,
        bootstrap_token: str | None = None,
        timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.bootstrap_token = bootstrap_token
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._bootstrapped = False
        self._session_mutations: dict[str, SessionSummary | SessionExport | None] = {}

    async def load(
        self,
        workspace: str | None,
        *,
        selected_session_id: str | None = None,
    ) -> NavigationSnapshot:
        await self._ensure_session()
        if workspace is None and selected_session_id is not None:
            selected_payload = await self._request(
                "GET",
                f"/api/sessions/{_path_identity(selected_session_id)}",
            )
            workspace = _optional_text(
                _mapping(selected_payload.get("session")).get("workspace")
            )
        query = urlencode({"workspace": workspace}) if workspace else ""
        (
            project_payload,
            sessions_payload,
            harness_payload,
            integration_payload,
        ) = await self._parallel_get(
            f"/api/project?{query}" if query else "/api/project",
            f"/api/sessions?{urlencode({'workspace': workspace, 'limit': MAX_SESSIONS})}"
            if workspace
            else f"/api/sessions?limit={MAX_SESSIONS}",
            "/api/harnesses",
            "/api/integrations",
        )
        project_data = _mapping(project_payload.get("project"))
        session_items = tuple(
            _session_summary_from_mapping(item)
            for item in _mapping_items(sessions_payload.get("sessions"), MAX_SESSIONS)
        )
        selected_id = _selected_summary_id(session_items, selected_session_id)
        selected_data = next(
            (
                item
                for item in _mapping_items(
                    sessions_payload.get("sessions"), MAX_SESSIONS
                )
                if str(item.get("id")) == selected_id
            ),
            None,
        )
        harnesses = tuple(
            _harness_summary(
                _mapping(item.get("spec")),
                _mapping(item.get("availability")),
                _mapping(item.get("workbench_transport")),
            )
            for item in _mapping_items(harness_payload.get("harnesses"), 100)
        )
        defaults = _mapping(project_payload.get("defaults"))
        harness_id = str(
            (selected_data or {}).get("default_harness_id")
            or defaults.get("harness")
            or "unknown"
        )
        model = _optional_text(
            (selected_data or {}).get("default_model") or defaults.get("model")
        )
        preflight = await self._request(
            "POST",
            "/api/preflight/run",
            {
                "session_id": selected_id,
                "workspace": str(project_data.get("root") or workspace or ""),
                "harness_id": harness_id,
                "model": model,
                "dry_run": True,
                "durable": False,
            },
        )
        readiness = _mapping(_mapping(preflight.get("preflight")).get("readiness"))
        try:
            environment_payload = await self._request(
                "GET",
                f"/api/environment?{urlencode({'workspace': project_data.get('root') or workspace or ''})}",
            )
            environment = _environment_summary_from_mapping(environment_payload)
        except (TypeError, ValueError, WorkbenchClientError) as exc:
            environment = EnvironmentSummary("unavailable", reason=str(exc)[:240])
        project = _project_summary_from_mapping(
            project_data,
            session_count=len(session_items),
        )
        return NavigationSnapshot(
            transport_mode=self.transport_mode,
            projects=(project,),
            project=project,
            sessions=session_items,
            selected_session_id=selected_id,
            harnesses=harnesses,
            readiness=_readiness_summary(
                readiness,
                session=selected_data,
                harnesses=harnesses,
                harness_id=harness_id,
                model=model,
            ),
            integrations=_integration_summary_from_mapping(integration_payload),
            environment=environment,
        )

    async def create_session(
        self,
        workspace: str,
        *,
        title: str | None = None,
        harness_id: str | None = None,
        model: str | None = None,
        api_mode: str | None = None,
        mode: str | None = None,
    ) -> SessionSummary:
        payload: dict[str, Any] = {"workspace": workspace}
        payload.update(
            {
                key: value
                for key, value in {
                    "title": title,
                    "harness_id": harness_id,
                    "model": model,
                    "api_mode": api_mode,
                    "mode": mode,
                }.items()
                if value is not None
            }
        )
        response = await self._request("POST", "/api/sessions", payload)
        return _session_summary_from_mapping(_mapping(response.get("session")))

    async def remember_session(self, workspace: str, session_id: str) -> None:
        await self._request(
            "PATCH",
            "/api/project/state",
            {"workspace": workspace, "last_selected_session": session_id},
        )

    async def search_sessions(
        self,
        query: str = "",
        *,
        provider: str | None = None,
        project: str | None = None,
        include_archived: bool = True,
    ) -> tuple[SessionSummary, ...]:
        values: dict[str, str | int] = {
            "include_archived": "true" if include_archived else "false",
            "limit": MAX_SESSIONS,
        }
        if query.strip():
            values["q"] = query.strip()
        if provider:
            values["harness_id"] = provider
        if project:
            values["project_id"] = project
        response = await self._request("GET", f"/api/sessions?{urlencode(values)}")
        return tuple(
            _session_summary_from_mapping(item)
            for item in _mapping_items(response.get("sessions"), MAX_SESSIONS)
        )

    async def preview_session(
        self, session_id: str, *, transcript_query: str = ""
    ) -> SessionPreview:
        values = {"q": transcript_query} if transcript_query.strip() else {}
        suffix = f"?{urlencode(values)}" if values else ""
        response = await self._request(
            "GET",
            f"/api/sessions/{_path_identity(session_id)}/navigation-preview{suffix}",
        )
        return _session_preview_from_mapping(response)

    async def rename_session(
        self, binding: SessionActionBinding, title: str
    ) -> SessionSummary:
        return await self._attached_session_patch(binding, {"title": title})

    async def archive_session(
        self, binding: SessionActionBinding, *, archived: bool = True
    ) -> SessionSummary:
        return await self._attached_session_patch(binding, {"archived": archived})

    async def delete_session(self, binding: SessionActionBinding) -> None:
        if binding.idempotency_key in self._session_mutations:
            return
        await self._request(
            "POST",
            f"/api/sessions/{_path_identity(binding.session_id)}/navigation-delete",
            _session_binding_payload(binding),
        )
        self._session_mutations[binding.idempotency_key] = None

    async def fork_session(self, binding: SessionActionBinding) -> SessionSummary:
        cached = self._session_mutations.get(binding.idempotency_key)
        if isinstance(cached, SessionSummary):
            return cached
        response = await self._request(
            "POST",
            f"/api/sessions/{_path_identity(binding.session_id)}/navigation-fork",
            _session_binding_payload(binding),
        )
        result = _session_summary_from_mapping(_mapping(response.get("session")))
        self._session_mutations[binding.idempotency_key] = result
        return result

    async def export_session(self, binding: SessionActionBinding) -> SessionExport:
        cached = self._session_mutations.get(binding.idempotency_key)
        if isinstance(cached, SessionExport):
            return cached
        response = await self._request(
            "POST",
            f"/api/sessions/{_path_identity(binding.session_id)}/navigation-export",
            _session_binding_payload(binding),
        )
        export = _mapping(response.get("export"))
        result = SessionExport(
            session_id=binding.session_id,
            path=_required_text(export.get("path"), "export path"),
            message_count=_bounded_non_negative_int(export.get("message_count")),
        )
        self._session_mutations[binding.idempotency_key] = result
        return result

    async def _attached_session_patch(
        self,
        binding: SessionActionBinding,
        patch: Mapping[str, Any],
    ) -> SessionSummary:
        cached = self._session_mutations.get(binding.idempotency_key)
        if isinstance(cached, SessionSummary):
            return cached
        response = await self._request(
            "POST",
            f"/api/sessions/{_path_identity(binding.session_id)}/navigation-update",
            {**patch, **_session_binding_payload(binding)},
        )
        result = _session_summary_from_mapping(_mapping(response.get("session")))
        self._session_mutations[binding.idempotency_key] = result
        return result

    async def _parallel_get(
        self,
        *paths: str,
    ) -> tuple[Mapping[str, Any], ...]:
        results: list[Mapping[str, Any] | None] = [None] * len(paths)

        async def fetch(index: int, path: str) -> None:
            results[index] = await self._request("GET", path)

        async with anyio.create_task_group() as group:
            for index, path in enumerate(paths):
                group.start_soon(fetch, index, path)
        return tuple(item or {} for item in results)

    async def _request_optional(self, method: str, path: str) -> Mapping[str, Any]:
        try:
            return await self._request(method, path)
        except WorkbenchClientError:
            return {}

    async def _ensure_session(self) -> None:
        if self._bootstrapped:
            return
        if self.bootstrap_token:
            await self._request(
                "POST",
                "/auth/session",
                authorization=f"Bearer {self.bootstrap_token}",
                ensure_session=False,
            )
        else:
            await self._request("GET", "/", ensure_session=False, expect_json=False)
        self._bootstrapped = True

    async def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        authorization: str | None = None,
        ensure_session: bool = True,
        expect_json: bool = True,
    ) -> Mapping[str, Any]:
        if ensure_session:
            await self._ensure_session()

        def send() -> Mapping[str, Any]:
            body = None
            headers = {"Accept": "application/json"}
            if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                headers["X-GigaLoom-CSRF"] = "1"
            if payload is not None:
                body = json.dumps(dict(payload), separators=(",", ":")).encode()
                headers["Content-Type"] = "application/json"
            if authorization:
                headers["Authorization"] = authorization
            request = Request(
                f"{self.base_url}{path}",
                data=body,
                headers=headers,
                method=method,
            )
            try:
                with self._opener.open(
                    request, timeout=self.timeout_seconds
                ) as response:
                    content = response.read(MAX_RESPONSE_BYTES + 1)
            except HTTPError as exc:
                raise WorkbenchClientError(
                    f"attach request rejected ({exc.code})"
                ) from exc
            except (OSError, URLError) as exc:
                raise WorkbenchClientError("attach endpoint is unavailable") from exc
            if len(content) > MAX_RESPONSE_BYTES:
                raise WorkbenchClientError("attach response exceeded the size limit")
            if not expect_json:
                return {}
            try:
                parsed = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorkbenchClientError("attach response is not valid JSON") from exc
            if not isinstance(parsed, Mapping):
                raise WorkbenchClientError("attach response must be an object")
            return parsed

        return await anyio.to_thread.run_sync(send)
