"""Bounded JSON upload and SSE transport for the gateway proxy."""

from __future__ import annotations

from collections.abc import Iterator
import json
from queue import Empty, Full, Queue
import secrets
import threading
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from gigaloom.types import GigaChatApiMode

from .models import ProxyRequestError
from .utils import _cancel_requested, _decode_sse_json, _read_error_body

_STREAM_POLL_SECONDS = 0.02
_STREAM_QUEUE_SIZE = 128


def upload_file(
    proxy_url: str,
    api_mode: GigaChatApiMode,
    *,
    filename: str,
    content: bytes,
    api_key: str | None = None,
    purpose: str = "assistants",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Upload one file through the OpenAI-compatible GigaChat Files route."""
    del api_mode
    safe_filename = filename.replace("\r", "").replace("\n", "") or "attachment"
    boundary = f"gpt2giga-{secrets.token_hex(16)}"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        f"{purpose}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; '
        f"filename*=UTF-8''{quote(safe_filename)}\r\n\r\n"
    ).encode("utf-8")
    body += content
    body += f"\r\n--{boundary}--\r\n".encode("ascii")
    headers = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key
    url = f"{proxy_url.rstrip('/')}/v1/files"
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read()
    except HTTPError as exc:
        error_body = _read_error_body(exc)
        message = f"proxy returned HTTP {exc.code} while uploading attachment"
        if error_body:
            message = f"{message}: {error_body}"
        raise ProxyRequestError(message, status_code=exc.code) from exc
    except URLError as exc:
        raise ProxyRequestError(f"proxy is not reachable: {exc.reason}") from exc
    try:
        decoded = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ProxyRequestError(
            "proxy returned non-JSON attachment upload response"
        ) from exc
    if not isinstance(decoded, dict):
        raise ProxyRequestError(
            "proxy returned attachment upload response that is not an object"
        )
    return decoded


def stream_sse_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float = 60.0,
    cancel_event: Any | None = None,
    idle_callback: Callable[[], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield decoded JSON objects from an SSE response."""
    if _cancel_requested(cancel_event):
        return
    body = None
    headers = {"Accept": "text/event-stream"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key
    request = Request(url, data=body, headers=headers, method=method.upper())
    data_lines: list[str] = []
    saw_event = False
    output_queue: Queue[tuple[str, Any]] = Queue(maxsize=_STREAM_QUEUE_SIZE)
    stop_event = threading.Event()
    response_holder: dict[str, Any] = {}

    def publish(kind: str, value: Any) -> None:
        while not stop_event.is_set():
            try:
                output_queue.put((kind, value), timeout=_STREAM_POLL_SECONDS)
                return
            except Full:
                continue

    def read_response() -> None:
        try:
            with urlopen(request, timeout=timeout) as response:
                response_holder["response"] = response
                for raw_line in response:
                    if stop_event.is_set():
                        break
                    publish("line", raw_line)
        except Exception as exc:
            publish("error", exc)
        finally:
            response_holder.pop("response", None)
            publish("done", None)

    reader = threading.Thread(
        target=read_response,
        name="gigaloom-sse-reader",
        daemon=True,
    )
    reader.start()
    try:
        while True:
            if _cancel_requested(cancel_event):
                return
            try:
                kind, value = output_queue.get(timeout=_STREAM_POLL_SECONDS)
            except Empty:
                if idle_callback is not None:
                    idle_callback()
                continue
            if kind == "done":
                break
            if kind == "error":
                raise value
            raw_line = value
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if not data_lines:
                    continue
                data = "\n".join(data_lines)
                data_lines.clear()
                if data == "[DONE]":
                    return
                yield _decode_sse_json(data)
                saw_event = True
                continue
            if line.startswith(":"):
                continue
            field, separator, field_value = line.partition(":")
            if field == "data":
                data_lines.append(field_value.lstrip(" ") if separator else "")
        if data_lines:
            data = "\n".join(data_lines)
            if data != "[DONE]":
                yield _decode_sse_json(data)
                saw_event = True
    except HTTPError as exc:
        error_body = _read_error_body(exc)
        message = f"proxy returned HTTP {exc.code}"
        if error_body:
            message = f"{message}: {error_body}"
        raise ProxyRequestError(message, status_code=exc.code) from exc
    except URLError as exc:
        raise ProxyRequestError(f"proxy is not reachable: {exc.reason}") from exc
    except (OSError, TimeoutError) as exc:
        raise ProxyRequestError(f"proxy stream failed: {exc}") from exc
    finally:
        stop_event.set()
        response = response_holder.get("response")
        close = getattr(response, "close", None)
        if callable(close):
            close()
        reader.join(timeout=_STREAM_POLL_SECONDS * 2)
    if not saw_event and not _cancel_requested(cancel_event):
        raise ProxyRequestError("proxy returned an empty SSE stream")
