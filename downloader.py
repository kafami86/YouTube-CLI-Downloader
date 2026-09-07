"""Download orchestration.

Bridges the UI and yt-dlp: builds format selectors from real yt-dlp formats,
runs downloads with real progress hooks, extracts metadata and locates the
final output files. All heavy lifting (network, fragments, retries, resume,
merging) is performed by yt-dlp + FFmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import Config
from utils import (
    codec_label,
    format_duration,
    format_date,
    format_views,
    human_size,
    log,
    log_exception,
    resolution_label,
)
from yt_dlp_manager import build_options, friendly_error, ytdlp_manager


class DownloadError(RuntimeError):
    """User-facing download failure with a clean, readable message."""


_AUDIO_EXTS: tuple[str, ...] = ("mp3", "m4a", "opus", "flac", "wav", "ogg")
_VIDEO_EXTS: tuple[str, ...] = ("mp4", "mkv", "webm")

# Format selectors. The MP4-friendly branches come first so that merged files
# stay playable everywhere; yt-dlp falls back automatically when unavailable.
BEST_SELECTOR: str = (
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo*+bestaudio/best"
)
_HEIGHT_PRESETS: tuple[int, ...] = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)
_HEIGHT_TAGS: dict[int, str] = {
    4320: "8K",
    2160: "4K",
    1440: "2K/QHD",
    1080: "Full HD",
    720: "HD",
}


# ------------------------------------------------------------------ data types
@dataclass(frozen=True)
class FormatRow:
    """One row of the advanced format viewer (data from yt-dlp itself)."""

    format_id: str
    ext: str
    resolution: str
    fps: str
    codec: str
    filesize: str


@dataclass(frozen=True)
class QualityChoice:
    """A selectable video quality derived from the video's real formats."""

    label: str
    height: int | None  # None = Best Available
    format_id: str = ""


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a download (finished, skipped or failed upstream)."""

    path: Path
    title: str
    quality: str
    container: str
    size: str
    skipped: bool = False


@dataclass(frozen=True)
class VideoInfo:
    """Metadata snapshot for the information view."""

    title: str
    uploader: str
    channel: str
    duration: str
    views: str
    upload_date: str
    description: str
    thumbnail: str
    webpage_url: str
    formats: list[FormatRow] = field(default_factory=list)
    qualities: list[QualityChoice] = field(default_factory=list)


# ------------------------------------------------------------------- selectors
def selector_for_height(height: int | None) -> str:
    """Map a quality choice onto a yt-dlp format selector string."""
    if height is None:
        return BEST_SELECTOR
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]/best"
    )


def selector_for_format_id(format_id: str) -> str:
    """Pin the selection to one concrete video format + best audio."""
    safe_id = str(format_id).strip()
    if not safe_id or not all(c.isalnum() or c in "-._" for c in safe_id):
        return BEST_SELECTOR
    return f"{safe_id}+bestaudio/{safe_id}/best"


# ------------------------------------------------------------------ extraction
def _make_ydl(options: dict[str, Any]) -> Any:
    return ytdlp_manager.module.YoutubeDL(options)


def extract_info(url: str, cfg: Config) -> dict[str, Any]:
    """Extract metadata for *url* without downloading anything."""
    options = build_options(
        cfg,
        None,
        outdir=Path("."),
        format_selector=None,
        noplaylist=True,
        with_postprocessors=False,
    )
    options.update({"skip_download": True, "extract_flat": False})
    try:
        with _make_ydl(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        log_exception("Metadata extraction failed")
        raise DownloadError(friendly_error(exc)) from exc
    if not info:
        raise DownloadError("Could not retrieve video information")
    if info.get("_type") == "playlist":
        # A playlist URL was passed; show its first video instead of failing.
        first = next((entry for entry in info.get("entries") or [] if entry), None)
        if first:
            info = first
        else:
            raise DownloadError("Playlist is empty or unavailable")
    return info


def playlist_entries(url: str, cfg: Config) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(playlist_title, entries)`` for a playlist URL."""
    options = build_options(
        cfg,
        None,
        outdir=Path("."),
        format_selector=None,
        noplaylist=False,
        with_postprocessors=False,
    )
    options.update({"skip_download": True, "extract_flat": "in_playlist"})
    try:
        with _make_ydl(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        log_exception("Playlist extraction failed")
        raise DownloadError(friendly_error(exc)) from exc
    if not info:
        raise DownloadError("Could not retrieve playlist information")
    entries = [entry for entry in (info.get("entries") or []) if entry]
    if not entries:
        raise DownloadError("Playlist is empty or unavailable")
    return str(info.get("title") or "Playlist"), entries


def build_video_info(info: dict[str, Any]) -> VideoInfo:
    """Convert a raw yt-dlp info dict into the UI-facing VideoInfo."""
    return VideoInfo(
        title=str(info.get("title") or "--"),
        uploader=str(info.get("uploader") or "--"),
        channel=str(info.get("channel") or info.get("uploader") or "--"),
        duration=format_duration(info.get("duration")),
        views=format_views(info.get("view_count")),
        upload_date=format_date(info.get("upload_date")),
        description=str(info.get("description") or ""),
        thumbnail=str(info.get("thumbnail") or "--"),
        webpage_url=str(info.get("webpage_url") or "--"),
        formats=collect_format_rows(info),
        qualities=available_qualities(info),
    )


def collect_format_rows(info: dict[str, Any]) -> list[FormatRow]:
    """Build the advanced format table directly from yt-dlp format dicts."""
    video_rows: list[tuple[int, FormatRow]] = []
    audio_rows: list[FormatRow] = []
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict) or fmt.get("format_id") is None:
            continue
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        row = FormatRow(
            format_id=str(fmt.get("format_id")),
            ext=str(fmt.get("ext") or "--"),
            resolution=resolution_label(fmt),
            fps=str(int(fmt["fps"])) if fmt.get("fps") else "--",
            codec=codec_label(fmt),
            filesize=human_size(size) if size else "--",
        )
        if row.resolution == "audio":
            audio_rows.append(row)
        else:
            # "1080p60" style labels: height = leading number.
            height = int("".join(c for c in row.resolution if c.isdigit()) or 0)
            video_rows.append((height, row))

    video_rows.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in video_rows] + audio_rows


def available_qualities(info: dict[str, Any]) -> list[QualityChoice]:
    """Derive the real, available quality list from the video's formats.

    Only heights that actually exist are offered, descending; an H.264 (AVC)
    stream is preferred per height for maximum MP4 compatibility, and a
    ``Best Available`` entry is always appended at the end.
    """
    best_by_height: dict[int, tuple[tuple[int, float], str]] = {}
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        height = fmt.get("height")
        vcodec = fmt.get("vcodec")
        if not height or vcodec in (None, "none"):
            continue
        height = int(height)
        tbr = float(fmt.get("tbr") or 0)
        is_avc = str(vcodec).lower().startswith("avc1")
        score = (1 if is_avc else 0, tbr)
        current = best_by_height.get(height)
        if current is None or score > current[0]:
            best_by_height[height] = (score, str(fmt.get("format_id")))

    choices: list[QualityChoice] = []
    for preset in _HEIGHT_PRESETS:
        if preset in best_by_height:
            tag = _HEIGHT_TAGS.get(preset, "")
            label = f"{preset}p" + (f"  {tag}" if tag else "")
            choices.append(
                QualityChoice(label=label, height=preset, format_id=best_by_height[preset][1])
            )
    choices.append(QualityChoice(label="Best Available", height=None))
    return choices


# ------------------------------------------------------------------ downloading
def _resolve_ffmpeg_dir(hooks: dict[str, Any] | None) -> Path | None:
    """FFmpeg location for yt-dlp: hooks override, else the discovered one."""
    configured = (hooks or {}).get("ffmpeg_dir")
    if configured is not None:
        return configured
    from ffmpeg_manager import ffmpeg_manager

    return ffmpeg_manager.exec_dir


def _probe_target_path(
    url: str,
    options: dict[str, Any],
    outdir: Path,
) -> tuple[Path, str]:
    """Predict the final output path using yt-dlp's own naming logic.

    Returns ``(predicted_path, title)``. All file-writing side effects
    (thumbnails, description, subtitles, info JSON) are disabled so the
    probe is purely informational.
    """
    probe_options = dict(options)
    probe_options.update({
        "skip_download": True,
        "quiet": True,
        "noprogress": True,
        "writethumbnail": False,
        "write_all_thumbnails": False,
        "writedescription": False,
        "writesubtitles": False,
        "writeinfojson": False,
        "writelink": False,
        "postprocessors": [],
    })
    with _make_ydl(probe_options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise DownloadError("Could not retrieve video information")
    base = str(info.get("title") or "Unknown")
    name = ydl.prepare_filename(info)  # yt-dlp's authoritative filename
    candidate = Path(name)
    predicted = candidate if candidate.is_absolute() else (outdir / candidate.name)
    return predicted, base


def _unique_path(target: Path) -> Path:
    """Find ``name (2).ext``, ``name (3).ext`` ... that does not exist yet."""
    for counter in range(2, 1000):
        candidate = target.with_name(f"{target.stem} ({counter}){target.suffix}")
        if not candidate.exists():
            return candidate
    return target


def _duplicate_action(
    target: Path,
    cfg: Config,
    hooks: dict[str, Any],
) -> str:
    """Decide how to handle an existing target file.

    Returns one of ``overwrite``, ``skip``, ``rename`` or ``proceed``
    (nothing exists / resume in progress). May invoke ``hooks['ask']``.
    """
    if not target.exists():
        return "proceed"
    behavior = cfg.overwrite_behavior
    if behavior == "overwrite":
        return "overwrite"
    if behavior == "rename":
        return "rename"
    if behavior == "skip":
        return "skip"
    if behavior == "redownload":
        return "overwrite"
    ask = hooks.get("ask")
    if callable(ask):
        return ask(target)
    return "proceed"  # default: let yt-dlp resume/skip safely


def download_video(
    url: str,
    cfg: Config,
    outdir: Path,
    *,
    quality_height: int | None = None,
    format_id: str = "",
    number: int | None = None,
    hooks: dict[str, Any] | None = None,
    overwrite_answer: bool | None = None,
) -> DownloadResult:
    """Download a video (merged to MP4 when streams are separate).

    ``number`` prefixes the filename for playlist ordering (``01 - Title.mp4``).
    """
    hooks = hooks or {}
    if hooks.get("status"):
        hooks["status"]("[cyan]→[/] Preparing download...")

    if format_id:
        selector = selector_for_format_id(format_id)
    else:
        selector = selector_for_height(quality_height)

    outtmpl = "%(title)s.%(ext)s"
    if number is not None:
        outtmpl = f"{number:02d} - %(title)s.%(ext)s"

    return _execute_download(
        url, cfg, outdir,
        outtmpl=outtmpl,
        format_selector=selector,
        audio=False,
        quality_label=(f"{quality_height}p" if quality_height else "Best"),
        hooks=hooks,
        overwrite_answer=overwrite_answer,
    )


def download_audio(
    url: str,
    cfg: Config,
    outdir: Path,
    *,
    quality: str = "0",
    number: int | None = None,
    hooks: dict[str, Any] | None = None,
    overwrite_answer: bool | None = None,
) -> DownloadResult:
    """Download the best audio stream and convert it via FFmpeg.

    ``quality`` is ``"0"`` (best VBR) or a kbps string such as ``"192"``.
    """
    hooks = hooks or {}
    if hooks.get("status"):
        hooks["status"]("[cyan]→[/] Preparing audio download...")

    quality = str(quality).strip() or "0"
    outtmpl = "%(title)s.%(ext)s"
    if number is not None:
        outtmpl = f"{number:02d} - %(title)s.%(ext)s"

    label = "Best" if quality in ("0", "best") else f"{quality} kbps"
    return _execute_download(
        url, cfg, outdir,
        outtmpl=outtmpl,
        format_selector="bestaudio/best",
        audio=True,
        quality_label=label,
        audio_extract=(cfg.default_audio_format, quality),
        hooks=hooks,
        overwrite_answer=overwrite_answer,
    )


def _execute_download(
    url: str,
    cfg: Config,
    outdir: Path,
    *,
    outtmpl: str,
    format_selector: str,
    audio: bool,
    quality_label: str,
    hooks: dict[str, Any],
    overwrite_answer: bool | None,
    audio_extract: tuple[str, str] | None = None,
) -> DownloadResult:
    """Shared download driver: resume, duplicate policy, yt-dlp run, result."""
    overwrite, resume = _overwrite_flags(cfg, overwrite_answer)
    options = build_options(
        cfg,
        _resolve_ffmpeg_dir(hooks),
        outdir=outdir,
        outtmpl=outtmpl,
        format_selector=format_selector,
        audio_extract=audio_extract,
        progress_hooks=[hooks["progress"]] if hooks.get("progress") else None,
        pp_hooks=[hooks["pp"]] if hooks.get("pp") else None,
        overwrite=overwrite,
        resume=resume,
        noplaylist=True,
    )

    # Resume + duplicate policy: predict the final path with yt-dlp's own
    # naming logic (only needed when the user must be consulted).
    if overwrite_answer is None:
        predicted, _ = _probe_target_path(url, options, outdir)

        part_file = predicted.with_suffix(".part")
        if part_file.exists():
            # Native yt-dlp resume; the prompt default follows the config.
            ask_resume = hooks.get("ask_resume")
            wants_resume = (callable(ask_resume) and ask_resume(part_file)) \
                or (not callable(ask_resume) and cfg.resume_enabled)
            if wants_resume:
                options["continuedl"] = True
                if hooks.get("status"):
                    hooks["status"]("[cyan]→[/] Resuming previous download")
            else:
                _remove_file(part_file)
                for stale in outdir.glob(part_file.name.replace(".part", ".part-*")):
                    _remove_file(stale)
                options["continuedl"] = False
                if hooks.get("status"):
                    hooks["status"]("[cyan]→[/] Starting fresh download")

        action = _duplicate_action(predicted, cfg, hooks)
        if action == "skip":
            if hooks.get("status"):
                hooks["status"](f"[yellow]![/] Already exists, skipped: {predicted.name}")
            return DownloadResult(predicted, predicted.stem, quality_label,
                                  predicted.suffix.lstrip('.').upper() or "--",
                                  human_size(predicted.stat().st_size
                                             if predicted.exists() else None),
                                  skipped=True)
        if action == "rename":
            renamed = _unique_path(predicted)
            options["outtmpl"] = str(renamed.name)
            if hooks.get("status"):
                hooks["status"](f"[cyan]→[/] Saving as: {renamed.name}")
        elif action == "overwrite":
            options["overwrites"] = True

    return _run_download(url, options, outdir, audio=audio,
                         quality_label=quality_label)


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove %s: %s", path, exc)


def _run_download(
    url: str,
    options: dict[str, Any],
    outdir: Path,
    *,
    audio: bool,
    quality_label: str,
) -> DownloadResult:
    """Execute a yt-dlp download and package the outcome."""
    try:
        with _make_ydl(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        log_exception("Download failed")
        raise DownloadError(friendly_error(exc)) from exc
    if not info:
        raise DownloadError("Download failed: no data returned by yt-dlp")
    return _result_from_info(info, outdir, audio=audio, quality_label=quality_label)


def _overwrite_flags(cfg: Config, overwrite_answer: bool | None) -> tuple[bool, bool]:
    """Translate the overwrite choice into yt-dlp (overwrites, continuedl)."""
    resume = bool(cfg.resume_enabled)
    if overwrite_answer is True:
        return True, resume
    return False, resume


# --------------------------------------------------------------- result parsing
def _find_downloaded_file(info: dict[str, Any], outdir: Path, audio: bool) -> Path | None:
    """Locate the final file yt-dlp produced for *info*."""
    for entry in info.get("requested_downloads") or []:
        for key in ("filepath", "_filename"):
            raw = entry.get(key)
            if raw:
                candidate = Path(str(raw))
                if candidate.is_file():
                    return candidate
    base = info.get("_filename") or info.get("filename")
    if base:
        candidate = Path(str(base))
        if candidate.is_file():
            return candidate
        stem = candidate.with_suffix("").name
        for ext in (_AUDIO_EXTS if audio else _VIDEO_EXTS):
            probe = outdir / f"{stem}.{ext}"
            if probe.is_file():
                return probe
    return None


def _result_from_info(
    info: dict[str, Any],
    outdir: Path,
    *,
    audio: bool,
    quality_label: str,
) -> DownloadResult:
    """Build a DownloadResult from a finished yt-dlp info dict."""
    path = _find_downloaded_file(info, outdir, audio)
    container = path.suffix.lstrip(".").upper() if path else str(info.get("ext") or "--")
    size = human_size(path.stat().st_size if path and path.is_file() else None)
    return DownloadResult(
        path=path or outdir,
        title=str(info.get("title") or "Unknown"),
        quality=quality_label,
        container=container or "--",
        size=size,
    )


# ------------------------------------------------------------------ resume utils
def check_resume_available(outdir: Path) -> bool:
    """True when incomplete .part files exist in *outdir*."""
    return outdir.is_dir() and any(outdir.glob("*.part"))


def cleanup_partials(directory: Path) -> None:
    """Delete leftover .part/.ytdl files from interrupted downloads."""
    if not directory.is_dir():
        return
    for pattern in ("*.part", "*.part-*", "*.ytdl"):
        for partial in directory.glob(pattern):
            try:
                if partial.is_file():
                    partial.unlink()
            except OSError as exc:
                log.warning("Could not remove partial file %s: %s", partial, exc)
