"""Bounded resource and application-context drawer screens."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListView, Static

from gpt2giga_harness.tui.widgets.navigation import NavigationItem


class ResourceDrawerScreen(ModalScreen[tuple[str, str] | None]):
    """Bounded keyboard-first task or process drawer."""

    CSS = """
    ResourceDrawerScreen { align: center middle; }
    #resource-dialog {
        width: 94%; height: 90%; padding: 1 2;
        border: round $accent; background: $surface;
    }
    #resource-body { height: 1fr; layout: horizontal; }
    #resource-list, #resource-detail { width: 1fr; height: 1fr; overflow: auto hidden; }
    #resource-detail { padding: 0 1; border-left: solid $primary-background; }
    #resource-actions { height: 3; align-horizontal: right; }
    #resource-actions Button { min-width: 10; margin-left: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Close", show=False)]

    def __init__(
        self,
        *,
        title: str,
        rows: tuple[tuple[str, str, str], ...],
        actions: tuple[tuple[str, str], ...],
        empty: str,
        close: str,
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.rows = rows
        self.actions = actions
        self.empty = empty
        self.close_label = close

    def compose(self) -> ComposeResult:
        with Vertical(id="resource-dialog"):
            yield Label(self.dialog_title, markup=False)
            with Horizontal(id="resource-body"):
                yield ListView(
                    *(
                        NavigationItem(label, identity)
                        for identity, label, _ in self.rows
                    ),
                    id="resource-list",
                )
                yield Static(self.empty, id="resource-detail", markup=False)
            with Horizontal(id="resource-actions"):
                for action, label in self.actions:
                    yield Button(label, id=f"resource-{action}")
                yield Button(self.close_label, id="resource-close", variant="primary")

    def on_mount(self) -> None:
        if self.rows:
            self.query_one("#resource-list", ListView).index = 0
            self._render_detail(self.rows[0][0])
        self.query_one("#resource-list", ListView).focus()

    @on(ListView.Highlighted, "#resource-list")
    def highlight_resource(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, NavigationItem):
            self._render_detail(event.item.value)

    @on(ListView.Selected, "#resource-list")
    def select_resource(self, event: ListView.Selected) -> None:
        if isinstance(event.item, NavigationItem):
            self.dismiss(("inspect", event.item.value))

    @on(Button.Pressed)
    def resource_action(self, event: Button.Pressed) -> None:
        if event.button.id == "resource-close":
            self.dismiss(None)
            return
        highlighted = self.query_one("#resource-list", ListView).highlighted_child
        if not isinstance(highlighted, NavigationItem):
            return
        action = str(event.button.id or "").removeprefix("resource-")
        self.dismiss((action, highlighted.value))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _render_detail(self, identity: str) -> None:
        detail = next(
            (body for key, _, body in self.rows if key == identity), self.empty
        )
        self.query_one("#resource-detail", Static).update(detail)


class ContextDrawerScreen(ModalScreen[str | None]):
    """Keyboard-first drawer for non-chat Workbench context."""

    CSS = """
    ContextDrawerScreen { align: right middle; }
    #context-dialog {
        width: 64;
        max-width: 96%;
        height: 100%;
        padding: 1 2;
        border-left: solid $accent;
        background: $surface;
    }
    #context-body { height: 1fr; layout: horizontal; }
    #context-list { width: 30; min-width: 24; height: 1fr; }
    #context-detail {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        border-left: solid $primary-background;
        overflow: auto hidden;
    }
    #context-drawer-actions { height: 3; align-horizontal: right; }
    #context-drawer-actions Button { min-width: 10; margin-left: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Close", show=False)]

    def __init__(
        self,
        rows: tuple[tuple[str, str, str], ...],
        *,
        title: str,
        open_label: str,
        close_label: str,
    ) -> None:
        super().__init__()
        self.rows = rows
        self.dialog_title = title
        self.open_label = open_label
        self.close_label = close_label

    def compose(self) -> ComposeResult:
        with Vertical(id="context-dialog"):
            yield Label(self.dialog_title, classes="dialog-title", markup=False)
            with Horizontal(id="context-body"):
                yield ListView(
                    *(
                        NavigationItem(label, command_id)
                        for command_id, label, _ in self.rows
                    ),
                    id="context-list",
                )
                yield Static("", id="context-detail", markup=False)
            with Horizontal(id="context-drawer-actions"):
                yield Button(self.close_label, id="context-close")
                yield Button(
                    self.open_label,
                    id="context-open",
                    variant="primary",
                )

    def on_mount(self) -> None:
        if self.rows:
            self.query_one("#context-list", ListView).index = 0
            self._render_detail(self.rows[0][0])
        self.query_one("#context-list", ListView).focus()

    @on(ListView.Highlighted, "#context-list")
    def highlight_context(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, NavigationItem):
            self._render_detail(event.item.value)

    @on(ListView.Selected, "#context-list")
    def select_context(self, event: ListView.Selected) -> None:
        if isinstance(event.item, NavigationItem):
            self.dismiss(event.item.value)

    @on(Button.Pressed, "#context-open")
    def open_context(self) -> None:
        highlighted = self.query_one("#context-list", ListView).highlighted_child
        if isinstance(highlighted, NavigationItem):
            self.dismiss(highlighted.value)

    @on(Button.Pressed, "#context-close")
    def close_context(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _render_detail(self, command_id: str) -> None:
        detail = next(
            (body for key, _, body in self.rows if key == command_id),
            "",
        )
        self.query_one("#context-detail", Static).update(detail)

    @property
    def body(self) -> str:
        """Return bounded text for tests and accessibility inspection."""
        return "\n".join(f"{label}\n{detail}" for _, label, detail in self.rows)
