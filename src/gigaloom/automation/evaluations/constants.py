"""Constants for the evals subcontext."""

from __future__ import annotations

from pathlib import Path
import re
from gigaloom.types import HarnessCapability


EVALS_RELATIVE_DIR = Path(".giga") / "evals"


EVAL_RUNS_DIR = "eval-runs"


EVAL_BASELINES_DIR = "eval-baselines"


DEFAULT_EVAL_HARNESSES = ("echo",)


MAX_EVAL_REPETITIONS = 20


EVAL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


CHECK_TYPES = {
    "contains",
    "not_contains",
    "contains_regex",
    "not_contains_regex",
    "equals",
}


ADAPTER_COMPATIBILITY_CHECKS = (
    "start",
    "stream",
    "tool_lifecycle",
    "failure",
    "cancel",
    "resume",
    "attachments",
    "managed_config",
)


PROTOCOL_CONFORMANCE_FIXTURES: tuple[tuple[str, HarnessCapability], ...] = (
    ("openai-chat", HarnessCapability.CHAT_COMPLETIONS),
    ("openai-responses", HarnessCapability.RESPONSES),
    ("anthropic-messages", HarnessCapability.ANTHROPIC_MESSAGES),
    ("gemini-generate-content", HarnessCapability.GEMINI_GENERATE_CONTENT),
)
