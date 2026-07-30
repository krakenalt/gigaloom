"""Durable worker CLI handlers."""

from __future__ import annotations

import argparse

from gpt2giga_harness.cli_commands.output import print_json
from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.runtime.store import RuntimeCoordinationStore
from gpt2giga_harness.runtime.worker import DurableJobWorker, worker_status


def _handle_worker_start(args: argparse.Namespace, config: HarnessConfig) -> int:
    worker = DurableJobWorker(
        config,
        lease_seconds=args.lease_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    if args.once:
        claimed = worker.run_once()
        worker.runtime_store.stop_worker(worker.worker_id)
        print("processed" if claimed else "idle")
        return 0
    print(f"Starting durable Harness worker {worker.worker_id}")
    print("Proxy auto-start is disabled; configure a running proxy/API key if needed.")
    try:
        worker.run_forever(
            poll_seconds=args.poll_seconds,
            max_idle_seconds=args.max_idle_seconds,
        )
    except KeyboardInterrupt:
        return 130
    return 0


def _handle_worker_status(args: argparse.Namespace, config: HarnessConfig) -> int:
    payload = worker_status(RuntimeCoordinationStore(config.data_dir))
    if args.json:
        print_json(payload)
    elif not payload["workers"]:
        print("No durable Harness workers registered.")
    else:
        print(f"Online workers: {payload['online']}")
        for worker in payload["workers"]:
            print(
                f"{worker['id']}  {worker['status']}  "
                f"pid={worker['process_id']}  heartbeat={worker['heartbeat_at']}"
            )
    return 0


def _handle_worker_stop_on_idle(args: argparse.Namespace, config: HarnessConfig) -> int:
    worker = DurableJobWorker(
        config,
        lease_seconds=args.lease_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    worker.run_forever(
        poll_seconds=args.poll_seconds,
        max_idle_seconds=args.max_idle_seconds,
        stop_on_idle_seconds=max(args.idle_seconds, 0.0),
    )
    print(f"Worker {worker.worker_id} stopped after idle timeout.")
    return 0
