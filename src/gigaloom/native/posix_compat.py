"""Optional POSIX modules used by native process control."""

from types import ModuleType


pty_module: ModuleType | None
try:  # pragma: no cover - import availability is platform-specific.
    import pty as _pty_module
except ImportError:  # pragma: no cover - Windows fallback.
    pty_module = None
else:
    pty_module = _pty_module

fcntl: ModuleType | None
termios: ModuleType | None
try:  # pragma: no cover - import availability is platform-specific.
    import fcntl as _fcntl
    import termios as _termios
except ImportError:  # pragma: no cover - Windows fallback.
    fcntl = None
    termios = None
else:
    fcntl = _fcntl
    termios = _termios
