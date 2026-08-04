#!/usr/bin/env python3
"""Hermetic ACP provider fixture that rejects session creation before setup."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def _send(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(request: dict[str, object], value: dict[str, object]) -> None:
    _send({"jsonrpc": "2.0", "id": request["id"], "result": value})


def _error(request: dict[str, object], message: str) -> None:
    _send(
        {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32000, "message": message},
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="normal")
    args, _ = parser.parse_known_args()
    if args.mode != "normal":
        Path.cwd().joinpath("fake-provider.pid").write_text(
            str(os.getpid()), encoding="utf-8"
        )
    current: dict[str, str] | None = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        params = request.get("params") or {}
        if method == "initialize":
            if args.mode == "initialize-failure":
                _error(request, "initialize rejected")
                continue
            _result(
                request,
                {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "provider-fixture", "version": "1.0.0"},
                    "agentCapabilities": {
                        "providers": {},
                        "promptCapabilities": {},
                    },
                },
            )
        elif method == "providers/list":
            effective = current
            if args.mode == "post-set-mismatch" and current is not None:
                effective = {**current, "baseUrl": "http://wrong.invalid/v1"}
            _result(
                request,
                {
                    "providers": [
                        {
                            "id": "main",
                            "supported": ["openai"],
                            "required": True,
                            "current": effective,
                        }
                    ]
                },
            )
        elif method == "providers/set":
            if args.mode == "reject-set":
                _error(request, "provider set rejected")
                continue
            current = {
                "apiType": params["apiType"],
                "baseUrl": params["baseUrl"],
            }
            _result(request, {})
        elif method == "session/new":
            if args.mode == "session-failure":
                _error(request, "session rejected")
                continue
            if current is None:
                _error(request, "provider must be configured before session")
                continue
            _result(
                request,
                {
                    "sessionId": "provider-session",
                    "configOptions": [
                        {
                            "id": "model",
                            "name": "Model",
                            "type": "select",
                            "currentValue": "provider-default",
                            "options": [
                                {
                                    "value": "GigaChat-2-Max",
                                    "name": "GigaChat-2-Max",
                                }
                            ],
                        }
                    ],
                },
            )
        elif method == "session/set_config_option":
            _result(
                request,
                {
                    "configOptions": [
                        {
                            "id": "model",
                            "name": "Model",
                            "type": "select",
                            "currentValue": params["value"],
                            "options": [
                                {"value": params["value"], "name": params["value"]}
                            ],
                        }
                    ]
                },
            )
        elif method == "session/prompt":
            if args.mode == "prompt-failure":
                _error(request, "prompt rejected")
                continue
            if args.mode == "hang-prompt":
                continue
            if args.mode == "permission-rejection":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": "permission-1",
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": "provider-session",
                            "toolCall": {
                                "toolCallId": "execute-1",
                                "kind": "execute",
                                "title": "Execute fixture",
                            },
                            "options": [
                                {
                                    "optionId": "deny",
                                    "name": "Deny",
                                    "kind": "reject_once",
                                }
                            ],
                        },
                    }
                )
                continue
            _send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "provider-session",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "routed"},
                        },
                    },
                }
            )
            _result(
                request,
                {
                    "stopReason": "end_turn",
                    "usage": {
                        "totalTokens": 3,
                        "inputTokens": 2,
                        "outputTokens": 1,
                    },
                },
            )


if __name__ == "__main__":
    main()
