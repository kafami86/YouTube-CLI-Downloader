"""YouTube-inspired red terminal theme for the CLI."""

from __future__ import annotations

from rich.theme import Theme

# Core palette
RED = "#ff0033"          # YouTube red, primary accent
RED_DIM = "#b3001f"      # dimmed red for borders / secondary accents
RED_BOLD = "bold #ff0033"
WHITE = "#f5f5f5"
GREY = "#9a9a9a"
DARK = "#1a1a1a"
GREEN = "#3fb950"
YELLOW = "#d29922"
CYAN = "#58a6ff"

TERMINAL_THEME: Theme = Theme(
    {
        "red": RED,
        "red.dim": RED_DIM,
        "red.bold": RED_BOLD,
        "white": WHITE,
        "grey": GREY,
        "dark": DARK,
        "green": GREEN,
        "yellow": YELLOW,
        "cyan": CYAN,
        "panel.border": RED_DIM,
        "panel.title": RED_BOLD,
        "table.header": RED_BOLD,
        "menu.key": RED_BOLD,
        "menu.label": WHITE,
        "status.ok": GREEN,
        "status.warn": YELLOW,
        "status.err": RED_BOLD,
        "progress.description": WHITE,
        "bar.complete": RED,
        "bar.finished": GREEN,
        "bar.pulse": RED_DIM,
        "spinner": RED,
    }
)
