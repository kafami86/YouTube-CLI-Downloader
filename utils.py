"""Shared helpers: filename sanitization, human formatting, logging, input."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import LOG_DIR

# Characters forbidden in Windows/OneDrive/Dropbox filenames.
_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Collapse runs of spaces produced by character replacement.
_MULTI_SPACE = re.compile(r"\s+")
# Strip dashes that replaced a forbidden char at the string edges.
_DANGLING_DASH = re.compile(r"^(?:\s*-\s*)+|(?:\s*-\s*)+$")
# A dash immediately before a file extension is always dangling (" - .mp4").
_DASH_BEFORE_DOT = re.compile(r"\s+-\s+(?=\.)")

log: logging.Logger = logging.getLogger("ytdl")


# --------------------------------------------------------------------- logging
def setup_logging(verbose: bool = False) -> None:
    """Route all internal logging to logs/app.log (never to the console)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    for handler in list(log.handlers):
        log.removeHandler(handler)
    handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    log.addHandler(handler)
    log.addHandler(logging.NullHandler())
    if verbose:
        # Debug mode mirrors log records to stderr, in dark gray.
        stream = logging.StreamHandler(stream=sys.stderr)
        stream.setLevel(logging.DEBUG)
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        log.addHandler(stream)
        handler.setLevel(logging.INFO)
    else:
        handler.setLevel(logging.DEBUG)
    log.propagate = False


def log_exception(message: str) -> None:
    """Log the active exception with full traceback for debugging."""
    log.exception(message)


# ------------------------------------------------------------------ sanitizing
def sanitize_filename(name: str, fallback: str = "untitled") -> str:
    """Make *name* safe as a Windows filename (no path traversal possible)."""
    cleaned = _INVALID_FS_CHARS.sub(" - ", str(name or ""))
    cleaned = _MULTI_SPACE.sub(" ", cleaned)
    cleaned = _DANGLING_DASH.sub("", cleaned)
    cleaned = _DASH_BEFORE_DOT.sub("", cleaned).strip(" .-")
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not cleaned or cleaned.upper() in reserved:
        cleaned = fallback
    return cleaned[:180]


def sanitize_template(template: str) -> str:
    """Sanitize every path segment of a yt-dlp output template."""
    return "/".join(sanitize_filename(part) for part in template.split("/"))


# ------------------------------------------------------------------- formatting
def human_size(num_bytes: float | None) -> str:
    """Format a byte count as a human-readable string (e.g. ``183.4 MB``)."""
    if not num_bytes or num_bytes <= 0:
        return "--"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024
    return "--"


def format_duration(seconds: float | int | None) -> str:
    """Format seconds as ``H:MM:SS`` or ``M:SS``."""
    if not seconds or seconds <= 0:
        return "--"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_eta(seconds: float | None) -> str:
    """Format yt-dlp's ETA value for the progress display."""
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_views(views: int | None) -> str:
    """Format a view count compactly, e.g. ``1.4M``."""
    if not views:
        return "--"
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if views >= divisor:
            return f"{views / divisor:.1f}{suffix}"
    return str(views)


def format_date(stamp: str | None) -> str:
    """Format a yt-dlp upload_date stamp (``YYYYMMDD``) as ``YYYY-MM-DD``."""
    raw = str(stamp or "").strip()
    return (
        f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
        if len(raw) == 8 and raw.isdigit()
        else (raw or "--")
    )


def resolution_label(fmt: dict) -> str:
    """Build a display label like ``1080p60`` from a yt-dlp format dict."""
    if fmt.get("vcodec") == "none" or not fmt.get("height"):
        return "audio"
    fps = fmt.get("fps") or 0
    base = f"{int(fmt['height'])}p"
    return f"{base}{int(fps)}" if fps and fps not in (24, 25, 30) else base


def codec_label(fmt: dict) -> str:
    """Extract a short codec name (``AVC``, ``VP9``, ``Opus``...) from a format."""
    for key in ("vcodec", "acodec"):
        raw = str(fmt.get(key) or "none")
        if raw != "none":
            codec = raw.split(".")[0].upper()
            mapping = {
                "AVC1": "AVC", "H264": "AVC", "H263": "H.263", "HEV1": "HEVC",
                "HVC1": "HEVC", "MP4A": "AAC", "EC-3": "E-AC3", "AC-3": "AC3",
                "VORBIS": "Vorbis",
            }
            return mapping.get(codec, codec.title() if codec.isalpha() else codec)
    return "--"


# ----------------------------------------------------------------- user input
def ask(prompt: str, default: str = "") -> str:
    """Prompt the user for a free-form string."""
    try:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{prompt}{suffix}\n> ").strip()
    except EOFError:
        return default
    return answer or default


def confirm(question: str, default_yes: bool = True) -> bool:
    """Ask a yes/no question; accepts plain Enter as the default."""
    try:
        hint = "Y/n" if default_yes else "y/N"
        answer = input(f"{question} [{hint}]\n> ").strip().lower()
    except EOFError:
        return default_yes
    if not answer:
        return default_yes
    return answer in ("y", "yes")


def choose(
    options: list[tuple[str, str]],
    title: str = "Select an option",
    allow_cancel: bool = True,
) -> str | None:
    """Render a numbered keyboard menu and return the chosen key.

    ``options`` is a list of ``(key, label)`` pairs. Returns ``None`` when the
    user cancels (empty input) and ``allow_cancel`` is true.
    """
    print(f"\n{title}")
    for key, label in options:
        print(f"  [{key}] {label}")
    keys = {str(key) for key, _ in options}
    while True:
        try:
            choice = input("> ").strip()
        except EOFError:
            return None
        if not choice and allow_cancel:
            return None
        if choice in keys:
            return choice
        print(f"  Invalid choice. Enter one of: {', '.join(sorted(keys))}")
