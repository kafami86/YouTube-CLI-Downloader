"""Interactive terminal UI: menus, prompts, panels and download flows.

All user interaction lives here. Download/processing work is delegated to
``downloader`` (which drives yt-dlp + FFmpeg); this module only renders state
and collects input.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

import downloader
import theme
from config import Config, save_config
from downloader import DownloadError, DownloadResult, VideoInfo
from utils import confirm, sanitize_filename

console = Console(theme=theme.TERMINAL_THEME)

MENU_OPTIONS: tuple[tuple[str, str], ...] = (
    ("1", "Download Video"),
    ("2", "Download MP3"),
    ("3", "Select Video Quality"),
    ("4", "Download Playlist"),
    ("5", "Video Information"),
    ("6", "Show Available Formats"),
    ("7", "Update yt-dlp"),
    ("8", "Update FFmpeg"),
    ("9", "Settings"),
    ("0", "Exit"),
)

AUDIO_QUALITY_MAP: dict[str, str] = {
    "1": "128", "2": "192", "3": "256", "4": "320", "5": "0",
}

_WIDTH = 48


# ------------------------------------------------------------------ primitives
def banner() -> Panel:
    """Render the application banner panel."""
    text = Text(justify="center")
    text.append("YOUTUBE CLI DOWNLOADER\n", style=theme.RED_BOLD)
    text.append("yt-dlp + FFmpeg", style=theme.GREY)
    return Panel(text, border_style=theme.RED, box=box.DOUBLE,
                 width=_WIDTH, padding=(0, 2))


def print_menu() -> None:
    """Render the main menu panel."""
    lines = Text()
    for key, label in MENU_OPTIONS:
        lines.append(f"[{key}] ", style=theme.RED_BOLD)
        lines.append(f"{label}\n", style=theme.WHITE)
    console.print()
    console.print(Panel(lines, title="MAIN MENU", title_align="left",
                        border_style=theme.RED_DIM, box=box.HEAVY,
                        width=_WIDTH, padding=(0, 2)))


def ask(prompt: str, default: str = "") -> str:
    """Free-form input with a red caret."""
    suffix = f" [dim][{default}][/]" if default else ""
    console.print(f"[bold white]{prompt}[/]{suffix}")
    try:
        answer = console.input("[bold red]> [/]")
    except (EOFError, KeyboardInterrupt):
        return default
    return answer.strip() or default


def choose(options: list[tuple[str, str]], title: str,
           default: str | None = None) -> str | None:
    """Numbered keyboard menu; returns the chosen key, or None on cancel."""
    keys = [key for key, _ in options]
    body = Text()
    for key, label in options:
        marker = "  [dim](current)[/]" if default == key else ""
        body.append(f"[{key}] ", style=theme.RED_BOLD)
        body.append(f"{label}{marker}\n", style=theme.WHITE)
    console.print(Panel(body, title=title, title_align="left",
                        border_style=theme.RED_DIM, box=box.HEAVY_EDGE,
                        width=_WIDTH, padding=(0, 2)))
    while True:
        try:
            answer = console.input("[bold red]> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not answer and default:
            return default
        if not answer:
            return None
        if answer in keys:
            return answer
        console.print(f"[yellow]Invalid choice. Enter one of: {', '.join(keys)}[/]")


def ok(message: str) -> None:
    console.print(f"[green]✓[/] {message}")


def warn(message: str) -> None:
    console.print(f"[yellow]![/] {message}")


def fail(message: str) -> None:
    console.print(f"[bold red]✗[/] {message}")


def info(message: str) -> None:
    console.print(f"[cyan]→[/] {message}")


def spin(message: str):
    """A red spinner status context manager."""
    return console.status(message, spinner="dots", spinner_style=theme.RED)


def progress_bar(label: str, steps: int = 20, delay: float = 0.02) -> None:
    """Short animated startup bar (cosmetic, terminal-safe)."""
    import time

    progress = Progress(
        TextColumn(f"[white]{label} "),
        BarColumn(bar_width=30, style=theme.DARK, complete_style=theme.RED),
        TaskProgressColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task = progress.add_task(label, total=steps)
        while not progress.finished:
            progress.advance(task)
            time.sleep(delay)


def pause() -> None:
    """Pause until the user presses Enter (also the menu-return affordance)."""
    try:
        console.input("\n[dim]Press Enter to return to the menu...[/]")
    except (EOFError, KeyboardInterrupt):
        pass
    console.print()


def handle_error(exc: BaseException) -> None:
    """Show a friendly error; never a raw traceback."""
    from utils import log_exception

    log_exception("UI action failed")
    fail(str(exc) or exc.__class__.__name__)
    console.print()


# --------------------------------------------------------------- shared helpers
def _ffmpeg_dir() -> Path | None:
    from ffmpeg_manager import ffmpeg_manager

    return ffmpeg_manager.exec_dir


def url_prompt(label: str = "YouTube URL") -> str | None:
    """Ask for a URL; empty input cancels (returns None)."""
    url = ask(label)
    if not url:
        return None
    if not (url.startswith(("http://", "https://")) or "." in url):
        fail("Invalid YouTube URL")
        return None
    return url


def fetch_video_info(url: str, cfg: Config) -> VideoInfo:
    """Fetch metadata via yt-dlp and build the UI object."""
    with spin("Fetching video information..."):
        raw = downloader.extract_info(url, cfg)
    return downloader.build_video_info(raw)


def quality_menu(video_info: VideoInfo) -> downloader.QualityChoice | None:
    """Show the qualities actually available for this video."""
    options = [(str(i), q.label) for i, q in enumerate(video_info.qualities, start=1)]
    if not options:
        fail("No downloadable video qualities found.")
        return None
    choice = choose(options, "AVAILABLE QUALITIES", default="1")
    if choice is None:
        return None
    return video_info.qualities[int(choice) - 1]


def audio_quality_menu() -> str:
    """Pick an audio bitrate; returns a yt-dlp quality value."""
    choice = choose(
        [(key, f"{value} kbps" if value != "0" else "Best Available")
         for key, value in AUDIO_QUALITY_MAP.items()],
        "AUDIO QUALITY",
        default="4",
    )
    return AUDIO_QUALITY_MAP[choice or "5"]


def ask_duplicate(existing: Path) -> str:
    """Prompt shown when the predicted output file already exists."""
    warn(f"File already exists: {existing.name}")
    choice = choose(
        [("1", "Skip"), ("2", "Overwrite"), ("3", "Redownload"), ("4", "Rename")],
        "FILE ALREADY EXISTS",
    )
    return {"1": "skip", "2": "overwrite", "3": "redownload",
            "4": "rename"}.get(choice or "1", "skip")


def ask_resume(part_file: Path) -> bool:
    """Prompt shown when an interrupted download exists for this video."""
    warn("Previous incomplete download detected.")
    return confirm("Resume download?", True)


def show_download_complete(result: DownloadResult, kind: str) -> None:
    """Render the DOWNLOAD COMPLETE panel (or the skipped notice)."""
    if result.skipped:
        info(f"File already exists, skipped: {result.path.name}")
        return
    lines = Text()
    lines.append("Title   : ", style=theme.GREY)
    lines.append(f"{result.title}\n", style=theme.WHITE)
    if kind == "video":
        lines.append("Quality : ", style=theme.GREY)
        lines.append(f"{result.quality}\n", style=theme.WHITE)
        lines.append("Format  : ", style=theme.GREY)
        lines.append(f"{result.container}\n", style=theme.WHITE)
    else:
        lines.append("Format  : ", style=theme.GREY)
        lines.append(f"{result.container}\n", style=theme.WHITE)
        lines.append("Quality : ", style=theme.GREY)
        lines.append(f"{result.quality}\n", style=theme.WHITE)
    lines.append("Size    : ", style=theme.GREY)
    lines.append(f"{result.size}\n", style=theme.WHITE)
    lines.append("\nSaved to:\n", style=theme.GREY)
    lines.append(str(result.path), style=theme.WHITE)
    console.print()
    console.print(Panel(
        lines,
        title=Text("DOWNLOAD COMPLETE", style=theme.RED_BOLD),
        border_style=theme.RED,
        box=box.DOUBLE,
        width=64,
        padding=(0, 2),
    ))


# ---------------------------------------------------------------- progress view
class DownloadProgress:
    """Renders yt-dlp's real progress hooks with a red bar + live status."""

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(style=theme.RED),
            TextColumn("[white]{task.description}"),
            BarColumn(bar_width=28, style=theme.DARK,
                      complete_style=theme.RED, finished_style=theme.GREEN),
            TaskProgressColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        )
        self._task_id: int | None = None
        self._live: Live | None = None
        self.status_line = Text("", style=theme.GREY)

    def __enter__(self) -> "DownloadProgress":
        self._live = Live(Group(self.status_line, self._progress),
                          console=console, refresh_per_second=12)
        self._live.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    def _set_status(self, message: str) -> None:
        # Mutate in place: the Live group holds a reference to this Text.
        plain = Text.from_markup(message).plain
        self.status_line.plain = plain

    # -- yt-dlp hooks ------------------------------------------------------
    def progress_hook(self, data: dict[str, Any]) -> None:
        """Real yt-dlp progress hook."""
        status = data.get("status")
        if status == "downloading":
            if self._task_id is None:
                info_dict = data.get("info_dict") or {}
                vcodec = str(info_dict.get("vcodec") or "")
                acodec = str(info_dict.get("acodec") or "")
                if acodec == "none":
                    description = "Downloading video"
                elif vcodec == "none":
                    description = "Downloading audio"
                else:
                    description = "Downloading"
                self._task_id = self._progress.add_task(description, total=None)
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            self._progress.update(self._task_id, total=total or None, completed=done)
        elif status == "finished":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            if self._task_id is not None:
                self._progress.update(self._task_id, total=total or None,
                                      completed=total or self._progress.tasks[self._task_id].completed)
            ok("Stream downloaded")
            self._task_id = None

    def pp_hook(self, data: dict[str, Any]) -> None:
        """Real yt-dlp postprocessor hook (merge/convert/embed)."""
        if str(data.get("when") or "") != "post_process":
            return
        name = str(data.get("postprocessor") or "")
        if "Merger" in name:
            console.print("[red]→[/] Merging with FFmpeg...")
        elif "ExtractAudio" in name:
            console.print("[red]→[/] Extracting audio with FFmpeg...")
        elif "Metadata" in name or "Thumbnail" in name:
            console.print(f"[dim]→[/] {name}...")

    def hooks(self, ffmpeg_dir: Path | None, ask_duplicate=None,
              ask_resume=None) -> dict[str, Any]:
        """Assemble the hook dict passed to downloader functions."""
        return {
            "progress": self.progress_hook,
            "pp": self.pp_hook,
            "status": self._set_status,
            "ffmpeg_dir": ffmpeg_dir,
            "ask": ask_duplicate,
            "ask_resume": ask_resume,
        }


# ==================================================================== app loop
def run(cfg: Config) -> None:
    """Main interactive loop; returns when the user exits."""
    from ffmpeg_manager import ffmpeg_manager
    from yt_dlp_manager import ytdlp_manager

    while True:
        print_menu()
        try:
            choice = console.input("[bold red]> [/]").strip().lstrip("\ufeff")
        except (EOFError, KeyboardInterrupt):
            choice = "0"

        if choice == "1":
            _action_download_video(cfg)
        elif choice == "2":
            _action_download_audio(cfg)
        elif choice == "3":
            _action_select_quality(cfg)
        elif choice == "4":
            _action_download_playlist(cfg)
        elif choice == "5":
            _action_video_info(cfg)
        elif choice == "6":
            _action_show_formats(cfg)
        elif choice == "7":
            _action_update_ytdlp(ytdlp_manager)
        elif choice == "8":
            _action_update_ffmpeg(ffmpeg_manager, cfg)
        elif choice == "9":
            _action_settings(cfg)
        elif choice in ("0", "q", "exit", "quit", ""):
            console.print("[dim]Goodbye![/]")
            return
        else:
            fail(f"Unknown option: {choice}")


# ---------------------------------------------------------------- video flows
def _action_download_video(cfg: Config, quality_height: int | None = None,
                           format_id: str = "") -> None:
    """Menu 1: pick URL + quality, download and merge to MP4."""
    try:
        url = url_prompt()
        if not url:
            return
        if quality_height is None and not format_id:
            video_info = fetch_video_info(url, cfg)
            quality = quality_menu(video_info)
            if quality is None:
                info("Cancelled.")
                return
            quality_height, format_id = quality.height, quality.format_id

        outdir = cfg.video_dir

        with DownloadProgress() as progress:
            hooks = progress.hooks(_ffmpeg_dir(), ask_duplicate, ask_resume)
            result = downloader.download_video(
                url, cfg, outdir,
                quality_height=quality_height, format_id=format_id,
                hooks=hooks,
            )
        show_download_complete(result, "video")
    except DownloadError as exc:
        handle_error(exc)
    except KeyboardInterrupt:
        warn("Download cancelled.")
    except Exception as exc:  # unexpected: log details, show clean message
        handle_error(exc)
    pause()


def _action_select_quality(cfg: Config) -> None:
    """Menu 3: quality-first flow, then download."""
    _action_download_video(cfg)


def _action_download_audio(cfg: Config) -> None:
    """Menu 2: audio download with bitrate choice."""
    try:
        url = url_prompt()
        if not url:
            return
        quality = audio_quality_menu()
        with DownloadProgress() as progress:
            hooks = progress.hooks(_ffmpeg_dir(), ask_duplicate, ask_resume)
            result = downloader.download_audio(
                url, cfg, cfg.audio_dir,
                quality=quality,
                hooks=hooks,
            )
        show_download_complete(result, "audio")
    except DownloadError as exc:
        handle_error(exc)
    except KeyboardInterrupt:
        warn("Download cancelled.")
    except Exception as exc:
        handle_error(exc)
    pause()


# ---------------------------------------------------------------- playlist flow
def _action_download_playlist(cfg: Config) -> None:
    """Menu 4: playlist download as video or MP3, numbered sequentially."""
    try:
        url = url_prompt("Playlist URL:")
        if not url:
            return
        with spin("Fetching playlist information..."):
            title, entries = downloader.playlist_entries(url, cfg)
        kind = choose(
            [("1", f"Video ({len(entries)} videos)"),
             ("2", f"MP3   ({len(entries)} items)")],
            f"PLAYLIST: {title[:28]}",
        )
        if kind is None:
            info("Cancelled.")
            return

        subfolder = (cfg.video_dir if kind == "1" else cfg.audio_dir) \
            / sanitize_filename(title, "Playlist")
        subfolder.mkdir(parents=True, exist_ok=True)
        info(f"Saving into: {subfolder}")

        completed = 0
        with DownloadProgress() as progress:
            hooks = progress.hooks(_ffmpeg_dir())

            def playlist_status(message: str) -> None:
                progress._set_status(f"[{completed + 1}/{len(entries)}] {message}")

            hooks["status"] = playlist_status
            for index, entry in enumerate(entries, start=1):
                entry_url = str(entry.get("url") or entry.get("webpage_url") or "")
                entry_title = str(entry.get("title") or f"Video {index}")
                if not entry_url:
                    fail(f"[{index}/{len(entries)}] {entry_title}: no URL")
                    continue
                try:
                    if kind == "1":
                        downloader.download_video(
                            entry_url, cfg, subfolder,
                            number=index, hooks=hooks,
                        )
                    else:
                        downloader.download_audio(
                            entry_url, cfg, subfolder,
                            quality="0", number=index, hooks=hooks,
                        )
                    completed += 1
                    ok(f"[{index}/{len(entries)}] {entry_title}")
                except DownloadError as exc:
                    fail(f"[{index}/{len(entries)}] {entry_title}: {exc}")
                except KeyboardInterrupt:
                    warn("Playlist interrupted by user.")
                    break

        console.print()
        lines = Text()
        lines.append("Playlist : ", style=theme.GREY)
        lines.append(f"{title}\n", style=theme.WHITE)
        lines.append("Completed: ", style=theme.GREY)
        lines.append(f"{completed}/{len(entries)}\n", style=theme.WHITE)
        lines.append("\nSaved to:\n", style=theme.GREY)
        lines.append(str(subfolder), style=theme.WHITE)
        console.print(Panel(lines, title=Text("PLAYLIST COMPLETE", style=theme.RED_BOLD),
                            border_style=theme.RED, box=box.DOUBLE, width=64, padding=(0, 2)))
    except DownloadError as exc:
        handle_error(exc)
    except KeyboardInterrupt:
        warn("Cancelled by user.")
    except Exception as exc:
        handle_error(exc)
    pause()


# ---------------------------------------------------------------- info flows
def _action_video_info(cfg: Config) -> None:
    """Menu 5: video information, no download."""
    try:
        url = url_prompt()
        if not url:
            return
        vi = fetch_video_info(url, cfg)
        _render_info_panel(vi)
    except DownloadError as exc:
        handle_error(exc)
    except Exception as exc:
        handle_error(exc)
    pause()


def _action_show_formats(cfg: Config) -> None:
    """Menu 6: metadata + real format table."""
    try:
        url = url_prompt()
        if not url:
            return
        vi = fetch_video_info(url, cfg)
        _render_info_panel(vi)
        console.print()
        table = Table(
            title=Text("AVAILABLE FORMATS", style=theme.RED_BOLD),
            box=box.SIMPLE_HEAVY,
            border_style=theme.RED_DIM,
            header_style=theme.RED_BOLD,
        )
        for column in ("ID", "EXT", "RESOLUTION", "FPS", "CODEC", "FILESIZE"):
            table.add_column(column)
        for row in vi.formats[:40]:
            table.add_row(row.format_id, row.ext, row.resolution,
                          row.fps, row.codec, row.filesize)
        console.print(table)
        if len(vi.formats) > 40:
            console.print(f"[dim]... {len(vi.formats) - 40} more formats[/]")
    except DownloadError as exc:
        handle_error(exc)
    except Exception as exc:
        handle_error(exc)
    pause()


def _render_info_panel(vi: VideoInfo) -> None:
    description = vi.description.strip() or "--"
    if len(description) > 400:
        description = description[:400].rsplit(" ", 1)[0] + " ..."
    lines = Text()
    for label, value in (
        ("Title", vi.title), ("Uploader", vi.uploader), ("Channel", vi.channel),
        ("Duration", vi.duration), ("Views", vi.views), ("Uploaded", vi.upload_date),
    ):
        lines.append(f"{label:<11}: ", style=theme.GREY)
        lines.append(f"{value}\n", style=theme.WHITE)
    lines.append("Description: ", style=theme.GREY)
    lines.append(f"{description}\n", style=theme.WHITE)
    lines.append("Thumbnail  : ", style=theme.GREY)
    lines.append(f"{vi.thumbnail}\n", style=theme.GREY)
    console.print(Panel(
        lines,
        title=Text("VIDEO INFORMATION", style=theme.RED_BOLD),
        border_style=theme.RED,
        box=box.DOUBLE,
        width=72,
        padding=(0, 2),
    ))


# ---------------------------------------------------------------- update flows
def _action_update_ytdlp(ytdlp_manager) -> None:
    """Menu 7: update the yt-dlp engine."""
    with spin("Updating yt-dlp..."):
        success = ytdlp_manager.update(status=lambda message: None)
    if success:
        ok(f"yt-dlp updated (version: {ytdlp_manager.version})")
        warn("Restart the application to load the new version cleanly.")
    else:
        fail("yt-dlp update failed. See logs/app.log")
    pause()


def _action_update_ffmpeg(ffmpeg_manager, cfg: Config) -> None:
    """Menu 8: show version and re-download the managed FFmpeg."""
    info(f"Current FFmpeg version: {ffmpeg_manager.info.version or 'not installed'}")
    with spin("Updating FFmpeg..."):
        success = ffmpeg_manager.update(status=lambda message: None)
    if success and ffmpeg_manager.exec_dir is not None:
        ok(f"FFmpeg updated (version: {ffmpeg_manager.info.version})")
    else:
        fail("FFmpeg update failed. See logs/app.log")
    pause()


# ---------------------------------------------------------------- settings
def _action_settings(cfg: Config) -> None:
    """Menu 9: interactive settings editor, persisted to config.json."""
    while True:
        rows = [
            ("default_video_quality", cfg.default_video_quality),
            ("default_audio_quality", cfg.default_audio_quality),
            ("default_audio_format", cfg.default_audio_format),
            ("video_output_directory", cfg.video_output_directory or "(project default)"),
            ("audio_output_directory", cfg.audio_output_directory or "(project default)"),
            ("overwrite_behavior", cfg.overwrite_behavior),
            ("resume_enabled", str(cfg.resume_enabled)),
            ("embed_thumbnail", str(cfg.embed_thumbnail)),
            ("embed_metadata", str(cfg.embed_metadata)),
            ("proxy", cfg.proxy or "(disabled)"),
            ("cookies_file", cfg.cookies_file or "(none)"),
            ("concurrent_fragments", str(cfg.concurrent_fragments)),
        ]
        lines = Text()
        for name, value in rows:
            lines.append(f"{name:<26} ", style=theme.GREY)
            lines.append(f"= {value}\n", style=theme.WHITE)
        console.print(Panel(lines, title="SETTINGS", title_align="left",
                            border_style=theme.RED_DIM, box=box.HEAVY_EDGE,
                            width=64, padding=(0, 2)))
        choice = choose(
            [("1", "Set default video quality"),
             ("2", "Set default audio quality / format"),
             ("3", "Set video output directory"),
             ("4", "Set audio output directory"),
             ("5", "Set overwrite behavior"),
             ("6", "Toggle resume"),
             ("7", "Toggle thumbnail / metadata embedding"),
             ("8", "Set proxy"),
             ("9", "Set cookies file"),
             ("10", "Set concurrent fragments"),
             ("b", "Back (changes are saved)")],
            "EDIT SETTINGS",
        )
        if choice is None or choice == "b":
            save_config(cfg)
            ok("Settings saved to config.json")
            console.print()
            return
        try:
            _edit_setting(cfg, choice)
            save_config(cfg)
        except (ValueError, OSError) as exc:
            fail(f"Invalid value: {exc}")


def _edit_setting(cfg: Config, choice: str) -> None:
    """Apply one settings edit based on the chosen menu key."""
    if choice == "1":
        value = ask("Video quality (best / 2160 / 1440 / 1080 / 720 / 480)",
                    cfg.default_video_quality).strip().lower().rstrip("p")
        cfg.default_video_quality = value if (value == "best" or value.isdigit()) \
            else "best"
    elif choice == "2":
        bitrate = ask("Audio quality (best / 128 / 192 / 256 / 320)",
                      cfg.default_audio_quality).strip().lower()
        cfg.default_audio_quality = bitrate if (bitrate == "best" or bitrate.isdigit()) \
            else "320"
        fmt = ask("Audio format (mp3 / m4a / opus / flac / wav)",
                  cfg.default_audio_format).strip().lower()
        cfg.default_audio_format = fmt
    elif choice == "3":
        cfg.video_output_directory = ask(
            "Video output directory (empty = project default)",
            cfg.video_output_directory).strip()
    elif choice == "4":
        cfg.audio_output_directory = ask(
            "Audio output directory (empty = project default)",
            cfg.audio_output_directory).strip()
    elif choice == "5":
        value = choose(
            [("1", "ask"), ("2", "skip"), ("3", "overwrite"),
             ("4", "redownload"), ("5", "rename")],
            "OVERWRITE BEHAVIOR",
            default={"ask": "1", "skip": "2", "overwrite": "3",
                     "redownload": "4", "rename": "5"}.get(cfg.overwrite_behavior, "1"),
        )
        if value:
            cfg.overwrite_behavior = {
                "1": "ask", "2": "skip", "3": "overwrite",
                "4": "redownload", "5": "rename",
            }[value]
    elif choice == "6":
        cfg.resume_enabled = not cfg.resume_enabled
        ok(f"Resume {'enabled' if cfg.resume_enabled else 'disabled'}")
    elif choice == "7":
        cfg.embed_thumbnail = not cfg.embed_thumbnail
        cfg.embed_metadata = cfg.embed_thumbnail
        ok(f"Embedding {'enabled' if cfg.embed_thumbnail else 'disabled'}")
    elif choice == "8":
        cfg.proxy = ask("Proxy (e.g. http://127.0.0.1:8080, empty = disabled)",
                        cfg.proxy).strip()
    elif choice == "9":
        value = ask("Cookies file path (Netscape format, empty = none)",
                    cfg.cookies_file).strip()
        if value and not Path(value).expanduser().is_file():
            warn("File does not exist; value saved anyway.")
        cfg.cookies_file = value
    elif choice == "10":
        value = ask("Concurrent fragments (1-16)", str(cfg.concurrent_fragments))
        cfg.concurrent_fragments = max(1, min(16, int(value or 4)))
