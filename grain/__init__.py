"""Grain: an offline voice assistant built on Needle (tool calling) and Whistle (speech to text).

Plugins use:
    from grain import tool          the @tool decorator
    from grain import open_target   open a program, file, URL or "start:<Start-menu name>"
    from grain import notify        show a tray notification
"""
__version__ = "1.1.0"

from .launch import notify, open_target  # noqa: E402
from .registry import Tool, tool  # noqa: E402

__all__ = ["Tool", "__version__", "notify", "open_target", "tool"]
