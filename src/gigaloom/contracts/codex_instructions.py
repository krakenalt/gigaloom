"""Dependency-light Codex developer-instruction composition contract."""

from __future__ import annotations

ASYNC_AGENT_RULES_VERSION = 1
MAX_DEVELOPER_INSTRUCTIONS_CHARACTERS = 20_000


def normalize_developer_instructions(value: str) -> str:
    """Validate and normalize user-authored developer instructions."""
    if not isinstance(value, str):
        raise TypeError("developer_instructions must be a string")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\0" in normalized:
        raise ValueError("developer_instructions cannot contain NUL")
    if len(normalized) > MAX_DEVELOPER_INSTRUCTIONS_CHARACTERS:
        raise ValueError("developer_instructions exceeds the accepted character limit")
    return normalized


def codex_developer_instructions(user_instructions: str) -> str:
    """Compose user text with versioned async-agent compatibility rules."""
    custom = normalize_developer_instructions(user_instructions)
    compatibility = f"""<async_agent_rules version=\"{ASYNC_AGENT_RULES_VERSION}\">
Apply this block only when the current turn lists collaboration tools named spawn_agent, send_input or send_message, and wait_agent. If those tools are absent, ignore this block.
Delivery of a task is not completion. A timeout or no update does not mean an agent is stuck.
After spawning an agent, do not send it another message by default.
Send one consolidated follow-up only for a new user requirement, a concrete blocking error, an agent question, or an unsafe action requiring correction.
After a follow-up, wait for a new result. Do not send message chains.
Without new user input, allow at most two correction cycles.
</async_agent_rules>"""
    return f"{custom}\n\n{compatibility}" if custom else compatibility
