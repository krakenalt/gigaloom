"""Expandable timeline widget for typed transcript events."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from textual import events
from textual.binding import Binding
from textual.widgets import Static

if TYPE_CHECKING:
    from gigaloom.tui.client import TimelineEvent


DEFAULT_VISIBLE_CARDS: Final[int] = 40
DEFAULT_MAX_ROWS: Final[int] = 200
DEFAULT_MAX_CHARS: Final[int] = 64_000


@dataclass(frozen=True, slots=True)
class TimelineRenderCounters:
    """Algorithmic work performed by one or more timeline projections."""

    events_inspected: int = 0
    cards_rendered: int = 0
    chars_produced: int = 0
    widget_updates: int = 0

    def __add__(
        self,
        other: TimelineRenderCounters,
    ) -> TimelineRenderCounters:
        return TimelineRenderCounters(
            events_inspected=self.events_inspected + other.events_inspected,
            cards_rendered=self.cards_rendered + other.cards_rendered,
            chars_produced=self.chars_produced + other.chars_produced,
            widget_updates=self.widget_updates + other.widget_updates,
        )

    def as_dict(self) -> dict[str, int]:
        """Return the stable counter names used by performance evidence."""
        return {
            "events_inspected": self.events_inspected,
            "cards_rendered": self.cards_rendered,
            "chars_produced": self.chars_produced,
            "widget_updates": self.widget_updates,
        }


@dataclass(frozen=True, slots=True)
class _TimelineCardBody:
    """Cached card content without active or expanded decoration."""

    summary: str
    detail: tuple[str, ...]


class TimelinePanel(Static):
    """Keyboard and mouse expandable typed transcript cards."""

    can_focus = True
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("up", "previous_card", "Previous", show=False),
        Binding("down", "next_card", "Next", show=False),
        Binding("enter", "toggle_card", "Expand", show=False),
        Binding("space", "toggle_card", "Expand", show=False),
    ]

    def __init__(
        self,
        empty: str,
        labels: dict[str, str],
        *,
        id: str,
        max_visible_cards: int = DEFAULT_VISIBLE_CARDS,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        if min(max_visible_cards, max_rows, max_chars) <= 0:
            raise ValueError("timeline render budgets must be positive")
        super().__init__(empty, id=id, markup=False)
        self.empty = empty
        self.labels = dict(labels)
        self.max_visible_cards = max_visible_cards
        self.max_rows = max_rows
        self.max_chars = max_chars
        self.events: tuple[TimelineEvent, ...] = ()
        self.active_index = 0
        self.expanded: set[str] = set()
        self.last_render_counters = TimelineRenderCounters()
        self.total_render_counters = TimelineRenderCounters()
        self._rendered_text = empty
        self._card_rows: list[tuple[int, int, int]] = []
        self._card_cache: OrderedDict[str, tuple[TimelineEvent, _TimelineCardBody]] = (
            OrderedDict()
        )
        self._cache_limit = max_visible_cards * 2

    def set_events(self, events: tuple[TimelineEvent, ...]) -> bool:
        """Replace the projection and redraw only its bounded visible window."""
        if events is self.events:
            self.last_render_counters = TimelineRenderCounters()
            return False
        was_empty = not self.events
        was_following_tail = (
            bool(self.events) and self.active_index == len(self.events) - 1
        )
        self.events = events
        if not events:
            self.active_index = 0
        elif was_empty or was_following_tail:
            self.active_index = len(events) - 1
        else:
            self.active_index = min(self.active_index, len(events) - 1)
        self._render_cards()
        return True

    def action_previous_card(self) -> None:
        """Move focus to the previous timeline card."""
        if not self.events or self.active_index == 0:
            return
        self.active_index -= 1
        self._render_cards()

    def action_next_card(self) -> None:
        """Move focus to the next timeline card."""
        if not self.events or self.active_index == len(self.events) - 1:
            return
        self.active_index += 1
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
            (index for index, start, end in self._card_rows if start <= event.y < end),
            self.active_index,
        )
        self.action_toggle_card()

    def _render_cards(self) -> None:
        if not self.events:
            self._publish_render(self.empty, (), events_inspected=0, cards_rendered=0)
            return

        start, end = self._window_bounds()
        reverse_groups: list[tuple[int, tuple[str, ...]]] = []
        events_inspected = 0
        cards_rendered = 0
        rows_used = 0
        chars_used = 0
        for index in range(end - 1, start - 1, -1):
            if rows_used >= self.max_rows or chars_used >= self.max_chars:
                break
            event = self.events[index]
            events_inspected += 1
            body, rendered = self._card_body(event)
            cards_rendered += int(rendered)
            card = self._decorate_card(
                body,
                active=index == self.active_index,
                expanded=event.id in self.expanded,
            )
            fitted: list[str] = []
            for line in card:
                if rows_used >= self.max_rows:
                    break
                separator_chars = int(rows_used > 0)
                remaining = self.max_chars - chars_used - separator_chars
                if remaining <= 0:
                    break
                clipped = line[:remaining]
                fitted.append(clipped)
                rows_used += 1
                chars_used += separator_chars + len(clipped)
                if len(clipped) != len(line):
                    break
            if not fitted:
                break
            reverse_groups.append((index, tuple(fitted)))
            if len(fitted) != len(card):
                break

        groups = tuple(reversed(reverse_groups))
        lines: list[str] = []
        card_rows: list[tuple[int, int, int]] = []
        for index, card in groups:
            row_start = len(lines)
            lines.extend(card)
            card_rows.append((index, row_start, len(lines)))
        text = "\n".join(lines) or self.empty
        self._publish_render(
            text,
            tuple(card_rows),
            events_inspected=events_inspected,
            cards_rendered=cards_rendered,
        )

    def _window_bounds(self) -> tuple[int, int]:
        count = len(self.events)
        tail_start = max(0, count - self.max_visible_cards)
        if self.active_index >= tail_start:
            return tail_start, count
        start = max(0, self.active_index - self.max_visible_cards // 2)
        end = min(count, start + self.max_visible_cards)
        return start, end

    def _card_body(
        self,
        event: TimelineEvent,
    ) -> tuple[_TimelineCardBody, bool]:
        cached = self._card_cache.get(event.id)
        if cached is not None and cached[0] == event:
            self._card_cache.move_to_end(event.id)
            return cached[1], False
        body = self._render_card_body(event)
        self._card_cache[event.id] = (event, body)
        self._card_cache.move_to_end(event.id)
        while len(self._card_cache) > self._cache_limit:
            self._card_cache.popitem(last=False)
        return body, True

    def _render_card_body(self, event: TimelineEvent) -> _TimelineCardBody:
        title = event.tool_name or event.message or event.type
        title = title.replace("\n", " ")[:160]
        category = _timeline_card_category(event)
        label = self.labels.get(category, category.upper())
        preview = ""
        if event.delta and event.delta.replace("\n", " ") != title:
            preview = f" · {event.delta.replace(chr(10), ' ')[:120]}"
        detail = (event.delta or event.message or "—")[: self.max_chars]
        detail_lines = tuple(
            f"    {line}" for line in (detail.splitlines() or ("—",))[: self.max_rows]
        )
        if event.stream:
            detail_lines += (f"    stream: {event.stream}",)
        if event.artifact_kind or event.artifact_id:
            detail_lines += (
                "    artifact: "
                f"{event.artifact_kind or 'artifact'} · "
                f"{event.artifact_id or 'authoritative run inspection'}",
            )
        if event.truncated:
            detail_lines += ("    preview truncated; open authoritative evidence",)
        return _TimelineCardBody(
            summary=f"[{label}] {title}{preview}",
            detail=detail_lines,
        )

    @staticmethod
    def _decorate_card(
        body: _TimelineCardBody,
        *,
        active: bool,
        expanded: bool,
    ) -> tuple[str, ...]:
        active_marker = ">" if active else " "
        expanded_marker = "−" if expanded else "+"
        summary = f"{active_marker}[{expanded_marker}] {body.summary}"
        return (summary, *body.detail) if expanded else (summary,)

    def _publish_render(
        self,
        text: str,
        card_rows: tuple[tuple[int, int, int], ...],
        *,
        events_inspected: int,
        cards_rendered: int,
    ) -> None:
        widget_updates = 0
        if text != self._rendered_text:
            self.update(text)
            self._rendered_text = text
            widget_updates = 1
        self._card_rows = list(card_rows)
        counters = TimelineRenderCounters(
            events_inspected=events_inspected,
            cards_rendered=cards_rendered,
            chars_produced=len(text),
            widget_updates=widget_updates,
        )
        self.last_render_counters = counters
        self.total_render_counters = self.total_render_counters + counters


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
