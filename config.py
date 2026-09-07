"""Persistent configuration management for YouTube CLI Downloader.

Configuration is stored in a human-readable ``config.json`` at the project
root. Missing or invalid values always fall back to safe defaults so a
corrupted config file can never crash the application.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

PROJECT_ROOT: Path = Path(__file__).resolve().parent
CONFIG_PATH: Path = PROJECT_ROOT / "config.json"

DEFAULT_VIDEO_DIR: Path = PROJECT_ROOT / "download" / "video"
DEFAULT_AUDIO_DIR: Path = PROJECT_ROOT / "download" / "audio"
BIN_DIR: Path = PROJECT_ROOT / "bin"
LOG_DIR: Path = PROJECT_ROOT / "logs"

OVERWRITE_BEHAVIORS: tuple[str, ...] = ("ask", "skip", "overwrite", "redownload", "rename")
AUDIO_FORMATS: tuple[str, ...] = ("mp3", "m4a", "opus", "flac", "wav")


def _as_bool(value: Any, default: bool) -> bool:
    """Coerce an arbitrary JSON value into a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    """Coerce an arbitrary JSON value into an int clamped to [low, high]."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


@dataclass
class Config:
    """Application settings persisted to config.json."""

    default_video_quality: str = "best"      # "best" or a height like "1080"
    default_audio_quality: str = "320"       # "best" or a bitrate like "192"
    default_audio_format: str = "mp3"
    video_output_directory: str = ""         # empty -> project download/video
    audio_output_directory: str = ""         # empty -> project download/audio
    overwrite_behavior: str = "ask"          # ask|skip|overwrite|redownload|rename
    resume_enabled: bool = True
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    proxy: str = ""                          # e.g. http://127.0.0.1:8080
    cookies_file: str = ""                   # Netscape-format cookies.txt
    ffmpeg_path: str = ""                    # optional explicit ffmpeg dir/file
    yt_dlp_path: str = ""                    # optional explicit yt-dlp package dir
    concurrent_fragments: int = 4

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _resolve_dir(value: str, default: Path) -> Path:
        """Resolve an output directory; relative paths anchor to the project."""
        if not value or not str(value).strip():
            path = default
        else:
            path = Path(str(value).strip()).expanduser()
            if not path.is_absolute():
                path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def video_dir(self) -> Path:
        return self._resolve_dir(self.video_output_directory, DEFAULT_VIDEO_DIR)

    @property
    def audio_dir(self) -> Path:
        return self._resolve_dir(self.audio_output_directory, DEFAULT_AUDIO_DIR)

    # ------------------------------------------------------------- validation
    def validate(self) -> None:
        """Normalize fields in place, repairing any invalid persisted values."""
        if self.default_video_quality != "best" and not str(
            self.default_video_quality
        ).isdigit():
            self.default_video_quality = "best"
        if self.default_audio_quality != "best" and not str(
            self.default_audio_quality
        ).isdigit():
            self.default_audio_quality = "320"
        self.default_audio_format = (
            self.default_audio_format.lower()
            if self.default_audio_format.lower() in AUDIO_FORMATS
            else "mp3"
        )
        if self.overwrite_behavior not in OVERWRITE_BEHAVIORS:
            self.overwrite_behavior = "ask"
        self.concurrent_fragments = _as_int(self.concurrent_fragments, 4, 1, 16)
        self.proxy = str(self.proxy or "").strip()
        self.cookies_file = str(self.cookies_file or "").strip()
        self.ffmpeg_path = str(self.ffmpeg_path or "").strip()
        self.yt_dlp_path = str(self.yt_dlp_path or "").strip()


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load configuration from disk, creating a default file when missing."""
    config = Config()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A broken config must never take the app down; log via defaults.
            data = {}
        if isinstance(data, dict):
            valid_names = {f.name for f in fields(Config)}
            for key, value in data.items():
                if key in valid_names:
                    setattr(config, key, value)
    config.validate()
    return config


def save_config(config: Config, path: Path = CONFIG_PATH) -> None:
    """Persist configuration as pretty-printed, human-readable JSON."""
    config.validate()
    path.write_text(
        json.dumps(asdict(config), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def ensure_directories() -> None:
    """Create the runtime directory layout (bin/, download/, logs/)."""
    for directory in (
        BIN_DIR / "yt-dlp",
        BIN_DIR / "ffmpeg",
        DEFAULT_VIDEO_DIR,
        DEFAULT_AUDIO_DIR,
        LOG_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
