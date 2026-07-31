"""Bounded file and session browser screens."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListView, Static

from gigaloom.tui.widgets.navigation import NavigationItem

if TYPE_CHECKING:
    from gigaloom.tui.client import FileCandidate, SessionSummary


class FilePickerScreen(ModalScreen[Any]):
    """Bounded keyboard-first project file picker with safe preview."""

    CSS = """
    FilePickerScreen { align: center middle; }
    #file-dialog {
        width: 92%;
        height: 88%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #file-policy { height: auto; color: $text-muted; }
    #file-list { height: 40%; margin-top: 1; border: round $primary-background; }
    #file-preview { height: 1fr; margin-top: 1; overflow: auto hidden; }
    #file-actions { height: 3; align-horizontal: right; }
    #file-actions Button { margin-left: 1; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        candidates: tuple[FileCandidate, ...],
        *,
        title: str,
        policy: str,
        attach: str,
        cancel: str,
        empty: str,
    ) -> None:
        super().__init__()
        self.candidates = candidates
        self.dialog_title = title
        self.policy = policy
        self.attach_label = attach
        self.cancel_label = cancel
        self.empty = empty

    def compose(self) -> ComposeResult:
        with Vertical(id="file-dialog"):
            yield Label(self.dialog_title, classes="dialog-title", markup=False)
            yield Static(self.policy, id="file-policy", markup=False)
            yield ListView(
                *(
                    NavigationItem(
                        f"{item.path} · {item.kind} · {item.size_bytes} B",
                        item.path,
                    )
                    for item in self.candidates
                ),
                id="file-list",
            )
            yield Static(self.empty, id="file-preview", markup=False)
            with Horizontal(id="file-actions"):
                yield Button(self.cancel_label, id="file-cancel")
                yield Button(
                    self.attach_label,
                    id="file-attach",
                    variant="primary",
                    disabled=not self.candidates,
                )

    def on_mount(self) -> None:
        view = self.query_one("#file-list", ListView)
        if self.candidates:
            view.index = 0
            self._render_preview(0)
        view.focus()

    @on(ListView.Highlighted, "#file-list")
    def highlight_file(self, event: ListView.Highlighted) -> None:
        item = event.item
        if not isinstance(item, NavigationItem):
            return
        index = next(
            (
                index
                for index, candidate in enumerate(self.candidates)
                if candidate.path == item.value
            ),
            None,
        )
        if index is not None:
            self._render_preview(index)

    @on(ListView.Selected, "#file-list")
    def select_file(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, NavigationItem):
            self.dismiss(
                next(
                    candidate
                    for candidate in self.candidates
                    if candidate.path == item.value
                )
            )

    @on(Button.Pressed, "#file-attach")
    def attach_file(self) -> None:
        index = self.query_one("#file-list", ListView).index
        if index is not None and 0 <= index < len(self.candidates):
            self.dismiss(self.candidates[index])

    @on(Button.Pressed, "#file-cancel")
    def cancel_file(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _render_preview(self, index: int) -> None:
        candidate = self.candidates[index]
        header = (
            f"{candidate.path}\n{candidate.mime_type} · {candidate.preview_status}\n\n"
        )
        self.query_one("#file-preview", Static).update(header + candidate.preview)


class SessionBrowserScreen(ModalScreen[Any]):
    """Search and preview bounded session projections across projects."""

    CSS = """
    SessionBrowserScreen {
        align: center middle;
    }
    #session-browser-dialog {
        width: 96%;
        max-width: 100;
        height: 92%;
        max-height: 32;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #session-browser-query {
        height: 3;
    }
    #session-browser-body {
        height: 1fr;
        layout: horizontal;
    }
    #session-browser-results, #session-browser-preview {
        width: 1fr;
        height: 1fr;
        overflow: auto hidden;
    }
    #session-browser-preview {
        padding: 0 1;
        border-left: solid $primary-background;
    }
    #session-browser-actions {
        height: 3;
        align-horizontal: right;
    }
    #session-browser-actions Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(
        self,
        sessions: tuple[SessionSummary, ...],
        *,
        title: str,
        placeholder: str,
        open_label: str,
        cancel_label: str,
        empty: str,
    ) -> None:
        super().__init__()
        self.sessions = sessions
        self.title = title
        self.placeholder = placeholder
        self.open_label = open_label
        self.cancel_label = cancel_label
        self.empty = empty
        self.filtered: tuple[SessionSummary, ...] = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="session-browser-dialog"):
            yield Label(self.title, markup=False)
            yield Input(placeholder=self.placeholder, id="session-browser-query")
            with Horizontal(id="session-browser-body"):
                yield ListView(id="session-browser-results")
                yield Static("", id="session-browser-preview", markup=False)
            with Horizontal(id="session-browser-actions"):
                yield Button(
                    self.open_label, id="session-browser-open", variant="primary"
                )
                yield Button(self.cancel_label, id="session-browser-cancel")

    def on_mount(self) -> None:
        self._apply_filter("")
        self.query_one("#session-browser-query", Input).focus()

    @on(Input.Changed, "#session-browser-query")
    def filter_sessions(self, event: Input.Changed) -> None:
        self._apply_filter(event.value)

    @on(ListView.Highlighted, "#session-browser-results")
    def preview_session(self, event: ListView.Highlighted) -> None:
        item = event.item
        if not isinstance(item, NavigationItem):
            return
        selected = next(
            (session for session in self.filtered if session.id == item.value), None
        )
        if selected is not None:
            self._render_preview(selected)

    @on(ListView.Selected, "#session-browser-results")
    def select_session(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, NavigationItem):
            self._dismiss_id(item.value)

    @on(Button.Pressed, "#session-browser-open")
    def open_session(self) -> None:
        highlighted = self.query_one(
            "#session-browser-results", ListView
        ).highlighted_child
        if isinstance(highlighted, NavigationItem):
            self._dismiss_id(highlighted.value)

    @on(Button.Pressed, "#session-browser-cancel")
    def cancel_session(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _dismiss_id(self, session_id: str) -> None:
        self.dismiss(
            next((item for item in self.filtered if item.id == session_id), None)
        )

    def _apply_filter(self, value: str) -> None:
        terms = value.casefold().split()

        def matches(session: SessionSummary) -> bool:
            searchable = " ".join(
                filter(
                    None,
                    (
                        session.title,
                        session.preview,
                        session.workspace,
                        session.project_id,
                        session.harness_id,
                        session.native_authority,
                        session.native_session_id,
                    ),
                )
            ).casefold()
            for term in terms:
                if term.startswith("provider:"):
                    provider = term.partition(":")[2]
                    if (
                        provider
                        not in " ".join(
                            filter(None, (session.harness_id, session.native_authority))
                        ).casefold()
                    ):
                        return False
                elif term.startswith("project:"):
                    project = term.partition(":")[2]
                    if (
                        project
                        not in " ".join(
                            filter(None, (session.project_id, session.workspace))
                        ).casefold()
                    ):
                        return False
                elif term == "archived:true":
                    if not session.archived:
                        return False
                elif term not in searchable:
                    return False
            return True

        self.filtered = tuple(item for item in self.sessions if matches(item))
        results = self.query_one("#session-browser-results", ListView)
        results.clear()
        for session in self.filtered:
            marker = "[archived] " if session.archived else ""
            results.append(NavigationItem(f"{marker}{session.title}", session.id))
        if self.filtered:
            results.index = 0
            self._render_preview(self.filtered[0])
        else:
            self.query_one("#session-browser-preview", Static).update(self.empty)

    def _render_preview(self, session: SessionSummary) -> None:
        native = (
            f"{session.native_authority}:{session.native_session_id} "
            f"({session.native_operation or 'linked'})"
            if session.native_session_id
            else "none"
        )
        self.query_one("#session-browser-preview", Static).update(
            "\n".join(
                (
                    session.title,
                    f"Provider: {session.harness_id}",
                    f"Project: {session.project_id or session.workspace or 'unbound'}",
                    f"Native session: {native}",
                    f"Revision: {session.revision}",
                    f"State: {'archived' if session.archived else 'active'}",
                    "",
                    session.preview or "No retained transcript preview.",
                )
            )
        )
