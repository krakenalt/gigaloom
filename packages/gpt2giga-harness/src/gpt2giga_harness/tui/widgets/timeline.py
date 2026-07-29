"""Expandable timeline widget for typed transcript events."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.binding import Binding
from textual.widgets import Static

if TYPE_CHECKING:
    from gpt2giga_harness.tui.client import TimelineEvent


class TimelinePanel(Static):
    """Keyboard and mouse expandable typed transcript cards."""

    can_focus = True
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("up", "previous_card", "Previous", show=False),
        Binding("down", "next_card", "Next", show=False),
        Binding("enter", "toggle_card", "Expand", show=False),
        Binding("space", "toggle_card", "Expand", show=False),
    ]

    def __init__(self, empty: str, labels: dict[str, str], *, id: str) -> None:
        super().__init__(empty, id=id, markup=False)
        self.empty = empty
        self.labels = labels
        self.events: tuple[TimelineEvent, ...] = ()
        self.active_index = 0
        self.expanded: set[str] = set()
        self._card_rows: list[tuple[int, int]] = []
        self._card_cache: dict[
            str, tuple[TimelineEvent, bool, bool, tuple[str, ...]]
        ] = {}

    def set_events(self, events: tuple[TimelineEvent, ...]) -> bool:
        """Replace the event projection and redraw changed cards."""
        if events == self.events:
            return False
        self.events = events
        self.active_index = min(self.active_index, max(len(events) - 1, 0))
        retained = {event.id for event in events}
        self.expanded.intersection_update(retained)
        self._card_cache = {
            event_id: cached
            for event_id, cached in self._card_cache.items()
            if event_id in retained
        }
        self._render_cards()
        return True

    def action_previous_card(self) -> None:
        """Move focus to the previous timeline card."""
        if self.events:
            self.active_index = max(self.active_index - 1, 0)
            self._render_cards()

    def action_next_card(self) -> None:
        """Move focus to the next timeline card."""
        if self.events:
            self.active_index = min(self.active_index + 1, len(self.events) - 1)
            self._render_cards()

    def action_toggle_card(self) -> None:
        """Expand or collapse the active timeline card."""
        if not self.events:
            return
        event_id = self.events[self.active_index].id
        if event_id in self.expanded:
            self.expanded.remove(event_id)
        else:
            self.expanded.add(event_id)
        self._render_cards()

    def on_click(self, event: events.Click) -> None:
        """Select and toggle the card at the clicked widget row."""
        self.focus()
        self.active_index = next(
            (
                index
                for index, (start, end) in enumerate(self._card_rows)
                if start <= event.y < end
            ),
            self.active_index,
        )
        self.action_toggle_card()

    def _render_cards(self) -> None:
        lines: list[str] = []
        self._card_rows = []
        for index, event in enumerate(self.events):
            start_row = len(lines)
            active = index == self.active_index
            expanded = event.id in self.expanded
            cached = self._card_cache.get(event.id)
            if (
                cached is None
                or cached[0] != event
                or cached[1] is not active
                or cached[2] is not expanded
            ):
                card = self._render_card(event, active=active, expanded=expanded)
                self._card_cache[event.id] = (event, active, expanded, card)
            else:
                card = cached[3]
            lines.extend(card)
            self._card_rows.append((start_row, len(lines)))
        self.update("\n".join(lines)[-64_000:] or self.empty)

    def _render_card(
        self,
        event: TimelineEvent,
        *,
        active: bool,
        expanded: bool,
    ) -> tuple[str, ...]:
        active_marker = ">" if active else " "
        expanded_marker = "−" if expanded else "+"
        title = event.tool_name or event.message or event.type
        title = title.replace("\n", " ")[:160]
        category = _timeline_card_category(event)
        label = self.labels.get(category, category.upper())
        preview = ""
        if event.delta and event.delta.replace("\n", " ") != title:
            preview = f" · {event.delta.replace(chr(10), ' ')[:120]}"
        lines = [f"{active_marker}[{expanded_marker}] [{label}] {title}{preview}"]
        if expanded:
            detail = event.delta or event.message or "—"
            lines.extend(f"    {line}" for line in detail.splitlines() or ("—",))
            if event.stream:
                lines.append(f"    stream: {event.stream}")
            if event.artifact_kind or event.artifact_id:
                lines.append(
                    "    artifact: "
                    f"{event.artifact_kind or 'artifact'} · "
                    f"{event.artifact_id or 'authoritative run inspection'}"
                )
            if event.truncated:
                lines.append("    preview truncated; open authoritative evidence")
        return tuple(lines)


def _timeline_card_category(event: TimelineEvent) -> str:
    if event.category != "status":
        return event.category
    normalized = event.type.lower()
    if "message" in normalized:
        return "message"
    if "reason" in normalized:
        return "reasoning"
    if "tool" in normalized:
        return "tool"
    if "approval" in normalized:
        return "approval"
    if "input" in normalized or "question" in normalized:
        return "question"
    if "warning" in normalized:
        return "warning"
    if "error" in normalized or "failed" in normalized:
        return "error"
    return "status"
