"""Automatic FFmpeg management.

Locates, verifies, downloads and updates a project-local FFmpeg build inside
``bin/ffmpeg/`` so the user never has to install FFmpeg manually. Windows is
the primary target; the URL table is structured so other platforms can be
added cleanly.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from config import BIN_DIR
from utils import log, log_exception

FFMPEG_DIR: Path = BIN_DIR / "ffmpeg"
_STATUS = Callable[[str], None]

# Official static builds, per platform. Keyed by (system, machine).
_FFMPEG_RELEASES: dict[tuple[str, str], str] = {
    ("win32", "amd64"): (
        "https://github.com/GyanD/codexffmpeg/releases/download/7.1/"
        "ffmpeg-7.1-essentials_build.zip"
    ),
    ("win32", "arm64"): (
        "https://github.com/GyanD/codexffmpeg/releases/download/7.1/"
        "ffmpeg-7.1-essentials_build.zip"
    ),
    ("linux", "x86_64"): (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
        "ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz"
    ),
    ("darwin", "arm64"): (
        "https://www.osxexperts.net/FFmpeg7Mac_arm.zip"
    ),
    ("darwin", "x86_64"): (
        "https://www.osxexperts.net/FFmpeg7Intel.zip"
    ),
}


@dataclass
class FFmpegInfo:
    """Discovery result for a usable FFmpeg installation."""

    path: Path | None      # directory that contains the executables
    version: str           # "7.1" or ""
    source: str            # "project-local", "system", "config", "missing"


class FFmpegManager:
    """Manage FFmpeg discovery, download and version reporting."""

    def __init__(self) -> None:
        self.info: FFmpegInfo = FFmpegInfo(None, "", "missing")
        self._searched: bool = False

    # ------------------------------------------------------------ discovery
    def _binary_names(self) -> tuple[str, str]:
        exe = ".exe" if sys.platform == "win32" else ""
        return f"ffmpeg{exe}", f"ffprobe{exe}"

    def _detect_version(self, ffmpeg: Path) -> str:
        """Return the first field of ``ffmpeg -version`` output, or ""."""
        try:
            result = subprocess.run(
                [str(ffmpeg), "-version"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            for line in result.stdout.splitlines():
                # e.g. "ffmpeg version 7.1-essentials_build-... Copyright"
                parts = line.split()
                if len(parts) >= 3 and parts[0] == "ffmpeg" and parts[1] == "version":
                    return parts[2].split("-")[0]
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("ffmpeg -version failed for %s: %s", ffmpeg, exc)
        return ""

    def _works(self, ffmpeg: Path) -> bool:
        return bool(self._detect_version(ffmpeg))

    def discover(self, configured_path: str = "") -> FFmpegInfo:
        """Locate a working FFmpeg: config override, project-local, then PATH."""
        self._searched = True
        names = self._binary_names()

        candidates: list[tuple[Path, str]] = []
        if configured_path:
            configured = Path(configured_path).expanduser()
            # Accept either the executable itself or a containing directory.
            for base in (configured, configured / names[0], configured / names[1]):
                if base.is_file():
                    candidates.append((base.parent, "config"))
                    break

        candidates.append((FFMPEG_DIR, "project-local"))

        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            candidates.append((Path(system_ffmpeg).parent, "system"))

        for directory, source in candidates:
            ffmpeg_bin = directory / names[0]
            if ffmpeg_bin.is_file() and self._works(ffmpeg_bin):
                self.info = FFmpegInfo(directory, self._detect_version(ffmpeg_bin), source)
                log.debug("FFmpeg found: %s (%s)", ffmpeg_bin, source)
                return self.info

        self.info = FFmpegInfo(None, "", "missing")
        return self.info

    # ------------------------------------------------------------- external
    def download(self, status: _STATUS = print) -> bool:
        """Download and install the correct static FFmpeg build for this OS."""
        key = (sys.platform, _machine())
        url = _FFMPEG_RELEASES.get(key)
        if not url:
            status(f"Unsupported platform for automatic FFmpeg install: {key}")
            log.error("No FFmpeg release mapped for %s", key)
            return False

        status(f"Downloading FFmpeg ({url.split('/')[-1]})...")
        FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        archive: Path | None = None
        try:
            from urllib.request import Request, urlopen

            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            archive = _temp_file_path(Path(url).suffix)
            with urlopen(request, timeout=60) as response, \
                    open(archive, "wb") as handle:
                _stream_to_file(response, handle, status)

            status("Extracting FFmpeg...")
            _extract(archive, FFMPEG_DIR)
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
            log_exception("FFmpeg download failed")
            status(f"Download failed: {exc}")
            _cleanup_partial()
            return False
        finally:
            if archive is not None:
                try:
                    archive.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("Could not remove temp archive: %s", exc)

        self.discover()
        if self.info.path is None:
            status("FFmpeg binaries not found after extraction.")
            _cleanup_partial()
            return False
        status(f"FFmpeg {self.info.version} installed into {FFMPEG_DIR.name}/.")
        return True

    def ensure(self, configured_path: str = "", status: _STATUS = print) -> bool:
        """Guarantee FFmpeg availability, downloading it when missing."""
        if self.discover(configured_path).path is not None:
            return True
        status("FFmpeg not found. Downloading required FFmpeg components...")
        return self.download(status)

    # -------------------------------------------------------------- updates
    def update(self, status: _STATUS = print) -> bool:
        """Re-download the project-local FFmpeg build."""
        _cleanup_partial()
        return self.download(status)

    @property
    def exec_dir(self) -> Path | None:
        """Directory containing ffmpeg/ffprobe; discovers on first use."""
        if not self._searched and self.info.path is None:
            self.discover()
        return self.info.path


def _machine() -> str:
    """Normalize ``platform.machine()`` to amd64/arm64/x86_64."""
    import platform

    norm = platform.machine().lower()
    if norm in ("amd64", "x86_64", "x64"):
        return "amd64" if sys.platform == "win32" else "x86_64"
    if "arm" in norm or "aarch" in norm:
        return "arm64"
    return norm or "unknown"


def _temp_file_path(suffix: str) -> Path:
    """Create a temp file path with its descriptor closed immediately."""
    descriptor, path_text = tempfile.mkstemp(suffix=suffix)
    import os

    os.close(descriptor)
    return Path(path_text)


def _stream_to_file(response: Any, handle: Any, status: _STATUS) -> None:
    """Stream the HTTP response to a file, reporting coarse progress."""
    total = int(response.headers.get("Content-Length") or 0)
    received = 0
    last_percent = -10
    while True:
        block = response.read(1 << 18)
        if not block:
            break
        handle.write(block)
        received += len(block)
        if total:
            percent = int(received * 100 / total)
            if percent >= last_percent + 10:
                last_percent = percent
                status(f"  {received / (1 << 20):.1f} MB ({percent}%)")


def _extract(archive: Path, destination: Path) -> None:
    """Extract a zip/tar release and flatten the executables to its root."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(destination)  # noqa: S202 - trusted fixed sources

    # Releases unpack into versioned subfolders (ffmpeg-7.1/bin/ffmpeg.exe,
    # ffmpeg-n7.1/bin/ffmpeg) or as bare binaries. Collect them at the root
    # so discovery only needs to look in one place.
    wanted = ("ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe")
    for executable in sorted(destination.rglob("*")):
        if executable.is_file() and executable.name.lower() in wanted:
            target = destination / executable.name
            if executable != target:
                shutil.move(str(executable), str(target))

    # Remove now-empty versioned subfolders.
    for entry in list(destination.iterdir()):
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)


def _cleanup_partial() -> None:
    """Remove a broken partial install so discovery won't trust it."""
    if FFMPEG_DIR.exists():
        shutil.rmtree(FFMPEG_DIR, ignore_errors=True)
        FFMPEG_DIR.mkdir(parents=True, exist_ok=True)


ffmpeg_manager = FFmpegManager()
