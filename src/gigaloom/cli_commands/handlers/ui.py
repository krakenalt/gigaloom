"""Web UI CLI handlers."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from typing import Any

from gigaloom.cli_commands.output import print_json
from gigaloom.config import HarnessConfig
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.runtime.worker import worker_status
from gigaloom.ui.remote_identity import (
    RemoteIdentityStore,
    RemoteOIDCSettings,
)


UI_WORKER_START_TIMEOUT_SECONDS = 10.0
UI_WORKER_STOP_TIMEOUT_SECONDS = 3.0
UI_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 5
MAX_UI_WORKER_COUNT = 32


class _LazyUvicorn:
    def run(self, *args: Any, **kwargs: Any) -> Any:
        import uvicorn as implementation

        return implementation.run(*args, **kwargs)


uvicorn = _LazyUvicorn()


def create_app(*args: Any, **kwargs: Any) -> Any:
    from gigaloom.ui.app import create_app as implementation

    return implementation(*args, **kwargs)


def validate_ui_bind(*args: Any, **kwargs: Any) -> Any:
    from gigaloom.ui.app import validate_ui_bind as implementation

    return implementation(*args, **kwargs)


def _handle_ui(args: argparse.Namespace, config: HarnessConfig) -> int:
    from gigaloom.ui.security import is_loopback_host

    config = config.with_overrides(ui_host=args.host, ui_port=args.port)
    validate_ui_bind(config, allow_remote=args.allow_remote)
    if not 1 <= args.worker_count <= MAX_UI_WORKER_COUNT:
        raise ValueError(
            f"UI worker count must be between 1 and {MAX_UI_WORKER_COUNT}."
        )
    ui_url = (
        config.ui_oidc_public_origin
        if not is_loopback_host(config.ui_host)
        else f"http://{config.ui_host}:{config.ui_port}"
    )
    print(f"Starting GigaLoom UI at {ui_url}/")
    worker_processes = (
        _start_ui_workers(config, worker_count=args.worker_count)
        if args.start_worker
        else ()
    )
    try:
        app = create_app(config)
        uvicorn.run(
            app,
            host=config.ui_host,
            port=config.ui_port,
            log_level="info",
            timeout_graceful_shutdown=UI_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
        )
    finally:
        _stop_ui_workers(worker_processes)
    return 0


def _handle_ui_identity_validate(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    settings = RemoteOIDCSettings.from_config(config)
    payload = {
        "valid": True,
        "issuer": settings.issuer,
        "public_origin": settings.public_origin,
        "callback_uri": settings.callback_uri,
        "roles": {
            "viewer": sum(role == "viewer" for role in settings.roles.values()),
            "operator": sum(role == "operator" for role in settings.roles.values()),
        },
        "trusted_proxy_count": len(settings.trusted_proxies),
        "client_secret_configured": True,
    }
    if args.json:
        print_json(payload)
    else:
        print(
            "Remote UI identity configuration is valid for "
            f"{settings.public_origin} ({len(settings.roles)} mapped subjects)."
        )
    return 0


def _handle_ui_identity_revoke_all(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    if not args.confirm:
        raise ValueError("Remote session revocation requires --confirm.")
    settings = RemoteOIDCSettings.from_config(config)
    revoked = RemoteIdentityStore(config.data_dir, settings).revoke_all()
    payload = {
        "revoked": revoked,
        "session_generation_rotated": True,
    }
    if args.json:
        print_json(payload)
    else:
        print(f"Revoked {revoked} remote UI session(s) and rotated the generation.")
    return 0


def _start_ui_workers(
    config: HarnessConfig,
    *,
    worker_count: int,
) -> tuple[subprocess.Popen[bytes], ...]:
    runtime_store = RuntimeCoordinationStore(config.data_dir)
    online = int(worker_status(runtime_store)["online"])
    missing = max(worker_count - online, 0)
    if missing == 0:
        print(f"Using {online} existing online durable Harness worker(s).")
        return ()
    if online:
        print(
            f"Using {online} existing online durable Harness worker(s); "
            f"starting {missing} more."
        )

    environment = os.environ.copy()
    environment.update(
        {
            "GIGALOOM_DATA_DIR": config.data_dir,
            "GIGALOOM_PROXY_URL": config.proxy_url,
            "GIGALOOM_DEFAULT_API_MODE": config.default_api_mode.value,
            "GIGALOOM_TIMEOUT_SECONDS": str(config.timeout_seconds),
            "GIGALOOM_AUTO_START_PROXY": "false",
        }
    )
    if config.api_key:
        environment["GIGALOOM_API_KEY"] = config.api_key
    if config.default_model:
        environment["GIGALOOM_DEFAULT_MODEL"] = config.default_model

    processes: list[subprocess.Popen[bytes]] = []
    try:
        for _ in range(missing):
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "gigaloom.cli",
                        "worker",
                        "start",
                    ],
                    env=environment,
                )
            except OSError as exc:
                raise ValueError(
                    f"Failed to start durable Harness worker: {exc}"
                ) from exc
            processes.append(process)
            _wait_for_ui_worker(runtime_store, process)
            print(f"Started durable Harness worker pid={process.pid}.")
    except Exception:
        _stop_ui_workers(processes)
        raise
    return tuple(processes)


def _wait_for_ui_worker(
    runtime_store: RuntimeCoordinationStore,
    process: subprocess.Popen[bytes],
) -> None:
    deadline = time.monotonic() + UI_WORKER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise ValueError(
                f"Durable Harness worker exited during startup with code {return_code}."
            )
        status = worker_status(runtime_store)
        if any(
            worker["status"] == "online" and int(worker["process_id"]) == process.pid
            for worker in status["workers"]
        ):
            return
        time.sleep(0.05)
    raise ValueError("Timed out waiting for the durable Harness worker to start.")


def _stop_ui_workers(
    processes: tuple[subprocess.Popen[bytes], ...] | list[subprocess.Popen[bytes]],
) -> None:
    for process in reversed(processes):
        _stop_ui_worker(process)


def _stop_ui_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            process.send_signal(signal.SIGINT)
        else:
            process.terminate()
        process.wait(timeout=UI_WORKER_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=UI_WORKER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=UI_WORKER_STOP_TIMEOUT_SECONDS)
