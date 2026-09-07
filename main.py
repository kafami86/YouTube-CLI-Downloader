"""Application entry point.

Runs dependency checks (Python, yt-dlp, FFmpeg, configuration) with a short
startup animation, then hands control to the interactive CLI.
"""

from __future__ import annotations

import argparse
import sys

from config import Config, ensure_directories, load_config
from utils import setup_logging

APP_VERSION = "1.0.0"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="youtube-cli", description="YouTube CLI downloader powered by yt-dlp + FFmpeg"
    )
    parser.add_argument("--version", action="store_true", help="show version and exit")
    parser.add_argument("--debug", action="store_true", help="mirror logs to stderr")
    return parser.parse_args(argv)


def check_python() -> str:
    """Verify the Python runtime meets the minimum requirement."""
    version_text = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 9):
        raise RuntimeError(f"Python 3.9+ is required (found {version_text})")
    return version_text


def _ensure_ytdlp(ytdlp_manager) -> str:
    if not ytdlp_manager.ensure_available(status=lambda message: None):
        raise RuntimeError(
            "yt-dlp could not be provisioned automatically.\n"
            "Install it manually with: pip install yt-dlp"
        )
    return ytdlp_manager.version


def _ensure_ffmpeg(ffmpeg_manager, cfg: Config) -> str:
    if ffmpeg_manager.ensure(cfg.ffmpeg_path, status=lambda message: None):
        return ffmpeg_manager.info.version
    return ""


def _ensure_downloader() -> str:
    """Verify the yt-dlp engine can actually be instantiated."""
    from yt_dlp_manager import ytdlp_manager

    with ytdlp_manager.module.YoutubeDL({"quiet": True, "no_warnings": True}):
        pass
    return "initialized"


def run_startup(cfg: Config) -> None:
    """Perform dependency checks with a short, terminal-safe animation."""
    import cli
    from ffmpeg_manager import ffmpeg_manager
    from yt_dlp_manager import ytdlp_manager

    console = cli.console
    console.print()
    console.print(cli.banner())
    console.print()

    steps: list[tuple[str, object]] = [
        ("Python", lambda: check_python()),
        ("yt-dlp", lambda: _ensure_ytdlp(ytdlp_manager)),
        ("FFmpeg", lambda: _ensure_ffmpeg(ffmpeg_manager, cfg)),
        ("Configuration", lambda: (ensure_directories(), "loaded")[1]),
        ("Downloader", lambda: _ensure_downloader()),
    ]

    for index, (name, action) in enumerate(steps, start=1):
        cli.progress_bar(f"Initializing YouTube Downloader ({index}/{len(steps)})")
        result = action()
        suffix = f" {result}" if result else ""
        cli.ok(f"{name}{suffix}")

    console.print()
    if ytdlp_manager.version:
        console.print(f"[dim]yt-dlp version: {ytdlp_manager.version}[/]")
    if ffmpeg_manager.exec_dir:
        console.print(f"[dim]FFmpeg version: {ffmpeg_manager.info.version or 'unknown'}[/]")
    console.print()
    console.print("[bold green]READY[/]")
    console.print()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.version:
        print(f"youtube-cli {APP_VERSION}")
        return 0

    setup_logging(verbose=args.debug)
    cfg = load_config()
    ensure_directories()

    import cli

    try:
        run_startup(cfg)
    except RuntimeError as exc:
        cli.fail(str(exc))
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:  # startup must never show a raw traceback
        from utils import log, log_exception

        log_exception("Startup failed")
        cli.fail(f"Startup failed: {exc}")
        cli.console.print("[dim]Details written to logs/app.log[/]")
        return 1

    try:
        cli.run(cfg)
    except KeyboardInterrupt:
        cli.console.print("\n[dim]Interrupted.[/]")
        return 130
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
