"""Navigation widgets shared by TUI screens and the main shell."""

from textual.widgets import Label, ListItem


class NavigationItem(ListItem):
    """List item that retains one opaque navigation identity."""

    def __init__(self, label: str, value: str) -> None:
        super().__init__(Label(label, markup=False))
        self.value = value
