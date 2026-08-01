"""Concrete, bounded Playwright adapter for the local Visual QA gate."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, cast

from gigaloom.automation.evaluations.visual.browser import (
    MAX_SCREENSHOT_BYTES,
    BrowserCaptureRequest,
    BrowserCaptureResult,
    BrowserConsoleLevel,
    BrowserConsoleObservation,
    BrowserDomObservation,
    BrowserRequestObservation,
)
from gigaloom.automation.evaluations.visual.contracts import BrowserFingerprint


_PLAYWRIGHT_ENV = "GIGALOOM_PLAYWRIGHT_PATH"
_MAX_BRIDGE_OUTPUT_BYTES = 32 * 1024 * 1024
_MAX_BROWSER_EXECUTABLE_BYTES = 1024 * 1024 * 1024

BridgeInvoker = Callable[[str, Mapping[str, Any] | None, float], Mapping[str, Any]]


class PlaywrightVisualBrowser:
    """Capture two isolated Chromium viewports through a content-free bridge."""

    def __init__(
        self,
        *,
        identity: BrowserFingerprint,
        executable_path: Path,
        executable_stat: tuple[int, int, int, int],
        invoke: BridgeInvoker,
    ) -> None:
        self.identity = identity
        self._executable_path = executable_path
        self._executable_stat = executable_stat
        self._invoke = invoke

    @classmethod
    def discover(
        cls,
        *,
        cwd: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> PlaywrightVisualBrowser:
        """Discover one already-installed local Playwright/Chromium runtime."""
        environment = dict(os.environ if environ is None else environ)
        node = shutil.which("node", path=environment.get("PATH"))
        if node is None:
            raise ValueError("Visual QA requires an installed Node.js executable")
        module_root = _resolve_playwright_root(
            cwd=(cwd or Path.cwd()),
            environ=environment,
        )
        runner = Path(__file__).with_name("_playwright_runner.mjs")
        invoke = _node_bridge(
            node=Path(node).resolve(),
            module_root=module_root,
            runner=runner,
            environ=environment,
        )
        metadata = _strict_mapping(
            invoke("fingerprint", None, 15.0),
            required={"automationVersion", "executablePath"},
            field_name="Playwright fingerprint",
        )
        executable_path = Path(
            _string(metadata["executablePath"], "browser executable path")
        ).resolve()
        executable_stat = _executable_stat(executable_path)
        executable_digest = _hash_executable(executable_path)
        if _executable_stat(executable_path) != executable_stat:
            raise ValueError("Visual QA browser executable changed during admission")
        browser_version = _browser_version(executable_path, environment)
        identity = BrowserFingerprint(
            engine="chromium",
            browser_version=browser_version,
            executable_digest=executable_digest,
            automation_name="playwright",
            automation_version=_string(
                metadata["automationVersion"],
                "Playwright automation version",
            ),
        )
        return cls(
            identity=identity,
            executable_path=executable_path,
            executable_stat=executable_stat,
            invoke=invoke,
        )

    def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
        """Capture one admitted viewport in a new browser process and context."""
        self._verify_executable_identity()
        viewport_count = len(request.admission.viewports)
        capture_lifetime_ms = max(
            request.admission.browser_lifetime_ms // viewport_count,
            1,
        )
        capture_request_limit = max(
            request.admission.max_requests // viewport_count,
            1,
        )
        payload = {
            "assertions": [
                {
                    "assertionId": item.assertion_id,
                    "kind": item.kind.value,
                    "selector": item.selector,
                }
                for item in request.assertions
            ],
            "deviceScaleFactor": request.viewport.device_scale_factor,
            "height": request.viewport.height,
            "maxRedirects": request.admission.max_redirects,
            "maxRequests": capture_request_limit,
            "origin": request.admission.grant.origin,
            "redactions": [
                {
                    "redactionId": item.redaction_id,
                    "required": item.required,
                    "selector": item.selector,
                }
                for item in request.redactions
            ],
            "targetUrl": request.admission.target_url,
            "timeoutMs": capture_lifetime_ms,
            "viewportId": request.viewport.viewport_id,
            "width": request.viewport.width,
        }
        raw = _strict_mapping(
            self._invoke(
                "capture",
                payload,
                capture_lifetime_ms / 1000 + 5.0,
            ),
            required={
                "console",
                "dom",
                "finalUrl",
                "maskedRedactionIds",
                "redirects",
                "requests",
                "screenshotBase64",
                "screenshotSecretScanPassed",
                "timingMs",
                "viewportId",
                "widths",
            },
            field_name="Playwright capture",
        )
        result = _decode_capture(raw, browser_fingerprint=self.identity.digest)
        self._verify_executable_identity()
        return result

    def _verify_executable_identity(self) -> None:
        if _executable_stat(self._executable_path) != self._executable_stat:
            raise ValueError("Visual QA browser executable changed after admission")


def _resolve_playwright_root(
    *,
    cwd: Path,
    environ: Mapping[str, str],
) -> Path:
    candidates: list[Path] = []
    configured = environ.get(_PLAYWRIGHT_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())
    search_roots = [cwd.resolve(), Path(__file__).resolve().parents[5]]
    for root in search_roots:
        for parent in (root, *tuple(root.parents)[:7]):
            candidates.extend(
                (
                    parent / "node_modules" / "playwright",
                    parent / "web" / "node_modules" / "playwright",
                )
            )
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (
            resolved.is_dir()
            and (resolved / "index.mjs").is_file()
            and (resolved / "package.json").is_file()
        ):
            return resolved
    raise ValueError(
        "Visual QA requires a local Playwright installation; run the Web dependency "
        f"install or set {_PLAYWRIGHT_ENV} to its package directory"
    )


def _node_bridge(
    *,
    node: Path,
    module_root: Path,
    runner: Path,
    environ: Mapping[str, str],
) -> BridgeInvoker:
    if not runner.is_file():
        raise ValueError("Visual QA Playwright bridge is missing")
    child_environment = {
        key: value
        for key, value in environ.items()
        if key
        in {
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "PLAYWRIGHT_BROWSERS_PATH",
            "TMPDIR",
        }
    }

    def invoke(
        mode: str,
        payload: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        encoded = b"" if payload is None else _canonical_json(payload)
        try:
            completed = subprocess.run(
                (str(node), str(runner), mode, str(module_root)),
                input=encoded,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout_seconds,
                env=child_environment,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError(
                "Visual QA browser exceeded its bounded lifetime"
            ) from error
        if completed.returncode != 0:
            raise ValueError("Visual QA browser adapter failed closed")
        if len(completed.stdout) > _MAX_BRIDGE_OUTPUT_BYTES:
            raise ValueError("Visual QA browser output exceeded its byte limit")
        try:
            decoded = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "Visual QA browser returned invalid bounded JSON"
            ) from error
        if not isinstance(decoded, Mapping):
            raise ValueError("Visual QA browser returned an invalid result")
        return cast(Mapping[str, Any], decoded)

    return invoke


def _decode_capture(
    raw: Mapping[str, Any],
    *,
    browser_fingerprint: str,
) -> BrowserCaptureResult:
    console_raw = _object_array(raw["console"], "browser console")
    request_raw = _object_array(raw["requests"], "browser requests")
    dom_raw = _object_array(raw["dom"], "browser DOM")
    console = tuple(
        sorted(
            (
                BrowserConsoleObservation(
                    level=BrowserConsoleLevel(
                        _string(
                            _strict_mapping(
                                item,
                                required={"level", "messageDigest"},
                                field_name="browser console observation",
                            )["level"],
                            "browser console level",
                        )
                    ),
                    message_digest=_string(
                        item["messageDigest"], "browser message digest"
                    ),
                )
                for item in console_raw
            ),
            key=lambda item: (item.level.value, item.message_digest),
        )
    )
    requests = tuple(
        sorted(
            (_decode_request(item) for item in request_raw),
            key=lambda item: (
                item.origin,
                item.url_digest,
                item.method,
                item.status_code or 0,
                item.failure_code or "",
                item.blocked,
            ),
        )
    )
    dom = tuple(
        sorted(
            (_decode_dom(item) for item in dom_raw),
            key=lambda item: item.assertion_id,
        )
    )
    screenshot_text = _string(raw["screenshotBase64"], "browser screenshot")
    if len(screenshot_text) > (MAX_SCREENSHOT_BYTES * 4 // 3) + 8:
        raise ValueError("Visual QA screenshot exceeded its encoded byte limit")
    try:
        screenshot = base64.b64decode(screenshot_text, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError(
            "Visual QA browser returned invalid screenshot bytes"
        ) from error
    widths = _strict_mapping(
        raw["widths"],
        required={"client", "scroll"},
        field_name="browser widths",
    )
    return BrowserCaptureResult(
        viewport_id=_string(raw["viewportId"], "browser viewport id"),
        final_url=_string(raw["finalUrl"], "browser final URL"),
        redirects=_string_tuple(raw["redirects"], "browser redirects"),
        browser_fingerprint=browser_fingerprint,
        screenshot_png=screenshot,
        console=console,
        requests=requests,
        dom=dom,
        client_width=_integer(widths["client"], "browser client width"),
        scroll_width=_integer(widths["scroll"], "browser scroll width"),
        timing_ms=_integer(raw["timingMs"], "browser timing"),
        masked_redaction_ids=_string_tuple(
            raw["maskedRedactionIds"],
            "browser masked redactions",
        ),
        screenshot_secret_scan_passed=_boolean(
            raw["screenshotSecretScanPassed"],
            "browser screenshot secret scan",
        ),
    )


def _decode_request(raw: Mapping[str, Any]) -> BrowserRequestObservation:
    value = _strict_mapping(
        raw,
        required={
            "blocked",
            "failureCode",
            "method",
            "origin",
            "statusCode",
            "urlDigest",
        },
        field_name="browser request observation",
    )
    status = value["statusCode"]
    failure = value["failureCode"]
    return BrowserRequestObservation(
        origin=_string(value["origin"], "browser request origin"),
        url_digest=_string(value["urlDigest"], "browser request URL digest"),
        method=_string(value["method"], "browser request method"),
        status_code=(
            None if status is None else _integer(status, "browser request status")
        ),
        failure_code=(
            None
            if failure is None
            else _string(failure, "browser request failure code")
        ),
        blocked=_boolean(value["blocked"], "browser request blocked state"),
    )


def _decode_dom(raw: Mapping[str, Any]) -> BrowserDomObservation:
    value = _strict_mapping(
        raw,
        required={"assertionId", "matchedCount", "textDigest", "visibleCount"},
        field_name="browser DOM observation",
    )
    text_digest = value["textDigest"]
    return BrowserDomObservation(
        assertion_id=_string(value["assertionId"], "browser assertion id"),
        matched_count=_integer(value["matchedCount"], "browser matched count"),
        visible_count=_integer(value["visibleCount"], "browser visible count"),
        text_digest=(
            None if text_digest is None else _string(text_digest, "browser text digest")
        ),
    )


def _browser_version(path: Path, environ: Mapping[str, str]) -> str:
    child_environment = {
        key: value
        for key, value in environ.items()
        if key in {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"}
    }
    try:
        completed = subprocess.run(
            (str(path), "--version"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
            env=child_environment,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Visual QA browser version probe timed out") from error
    try:
        version = completed.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValueError("Visual QA browser version is invalid") from error
    if completed.returncode != 0 or not version or len(version) > 128:
        raise ValueError("Visual QA browser version probe failed")
    return version


def _hash_executable(path: Path) -> str:
    if path.stat().st_size > _MAX_BROWSER_EXECUTABLE_BYTES:
        raise ValueError("Visual QA browser executable exceeds its byte limit")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _executable_stat(path: Path) -> tuple[int, int, int, int]:
    try:
        value = path.stat()
    except OSError as error:
        raise ValueError("Visual QA browser executable is unavailable") from error
    if not path.is_file():
        raise ValueError("Visual QA browser executable is not a regular file")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_mapping(
    value: object,
    *,
    required: set[str],
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError(f"{field_name} has an invalid schema")
    return cast(Mapping[str, Any], value)


def _object_array(value: object, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if (
        not isinstance(value, list)
        or len(value) > 512
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ValueError(f"{field_name} must be a bounded object array")
    return tuple(cast(Mapping[str, Any], item) for item in value)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > 512
        or any(not isinstance(item, str) for item in value)
    ):
        raise ValueError(f"{field_name} must be a bounded string array")
    return tuple(cast(str, item) for item in value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


__all__ = ["PlaywrightVisualBrowser"]
