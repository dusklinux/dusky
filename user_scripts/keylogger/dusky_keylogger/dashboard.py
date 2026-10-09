"""Deprecated shim — use dashboard_tui.py (Rich + matugen).

This file exists for backward compatibility for any external import of
`dusky_keylogger.dashboard`. New code should import `dashboard_tui`.
"""

lazy from .dashboard_tui import main

__all__ = ["main"]
