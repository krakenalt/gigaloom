#!/usr/bin/env python3
"""Hostile-capable stdlib-only ACP fixture; never contacts a provider."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def send(value: dict) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request: dict, value: dict) -> None:
    send({"jsonrpc": "2.0", "id": request["id"], "result": value})


def initialize(request: dict, mode: str, stream_done_file: str | None) -> None:
    if mode == "banner":
        sys.stdout.write("ACP agent ready\n")
        sys.stdout.flush()
    elif mode == "malformed":
        sys.stdout.write("{broken\n")
        sys.stdout.flush()
    elif mode == "oversized":
        sys.stdout.write("x" * (5 * 1024 * 1024) + "\n")
        sys.stdout.flush()
    elif mode == "wrong-id":
        send({"jsonrpc": "2.0", "id": "wrong", "result": {}})
        return
    if mode == "stderr-flood":
        sys.stderr.write("s" * (256 * 1024))
        sys.stderr.flush()
    result(
        request,
        {
            "protocolVersion": 1,
            "agentInfo": {"name": "fake-read-only", "version": "1.0.0"},
            "agentCapabilities": {
                "promptCapabilities": {},
                "sessionCapabilities": {"close": {}},
            },
            "authMethods": [],
        },
    )
    if mode == "stream":
        for index in range(1000):
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "unbound-hostile-session",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": str(index)},
                        },
                    },
                }
            )
        if stream_done_file:
            Path(stream_done_file).write_text("done", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="normal")
    parser.add_argument("--child-pid-file")
    parser.add_argument("--stream-done-file")
    args = parser.parse_args()
    if args.mode == "fork":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        Path(args.child_pid_file).write_text(str(child.pid), encoding="utf-8")
    if args.mode == "home-write":
        Path(os.environ["HOME"], "fixture-write").write_text(
            "isolated", encoding="utf-8"
        )
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        if method == "initialize":
            initialize(request, args.mode, args.stream_done_file)
        elif method == "session/new":
            result(request, {"sessionId": "fake-session"})
        elif method == "session/prompt":
            if args.mode == "ignore-cancel":
                continue
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "fake-session",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "read-1",
                            "kind": "read",
                            "title": "Read fixture",
                            "status": "completed",
                        },
                    },
                }
            )
            result(
                request,
                {
                    "stopReason": "end_turn",
                    "usage": {"totalTokens": 3, "inputTokens": 2, "outputTokens": 1},
                },
            )
        elif method == "session/cancel" and args.mode == "ignore-cancel":
            time.sleep(60)


if __name__ == "__main__":
    main()
