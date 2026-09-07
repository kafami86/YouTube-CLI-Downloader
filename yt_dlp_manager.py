"""yt-dlp integration layer.

Responsibilities:
* bootstrap a project-local official yt-dlp (inside ``bin/yt-dlp/``) when the
  Python package is not installed, so no global install is required,
* report and update the yt-dlp version,
* build yt-dlp option dictionaries from application configuration,
* translate yt-dlp errors into user-friendly messages.

yt-dlp itself remains the download engine: format selection, downloading,
fragments, retries, resume, playlists, metadata and post-processing are all
performed by ``yt_dlp.YoutubeDL``.
"""

from __future__ import annotations

import importlib
import io
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from config import BIN_DIR, Config
from utils import log, log_exception

YTDLP_DIR: Path = BIN_DIR / "yt-dlp"
_OFFICIAL_TARBALL: str = (
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.tar.gz"
)
_MAX_DOWNLOAD_BYTES: int = 80 * 1024 * 1024

_STATUS = Callable[[str], None]


class DependencyError(RuntimeError):
    """A required external dependency could not be provisioned."""


class _YtDlpLogAdapter:
    """Route yt-dlp's internal chatter into the application log file."""

    def __init__(self) -> None:
        self._log = log

    def debug(self, msg: str) -> None:  # noqa: D102 - protocol method
        if msg:
            self._log.debug("[yt-dlp] %s", msg)

    def info(self, msg: str) -> None:  # noqa: D102
        if msg:
            self._log.info("[yt-dlp] %s", msg)

    def warning(self, msg: str) -> None:  # noqa: D102
        if msg:
            self._log.warning("[yt-dlp] %s", msg)

    def error(self, msg: str) -> None:  # noqa: D102
        if msg:
            self._log.error("[yt-dlp] %s", msg)


class YtDlpManager:
    """Provide an importable yt-dlp module and manage its lifecycle."""

    def __init__(self) -> None:
        self._module: Any | None = None
        self._version: str = ""

    # ------------------------------------------------------------- accessors
    @property
    def module(self) -> Any:
        """The imported ``yt_dlp`` module (raises if not provisioned)."""
        if self._module is None:
            raise DependencyError(
                "yt-dlp is not available. Restart the application to retry setup."
            )
        return self._module

    @property
    def version(self) -> str:
        return self._version

    # ------------------------------------------------------------- provision
    def ensure_available(self, status: _STATUS = print) -> bool:
        """Guarantee yt-dlp is importable, bootstrapping it when missing.

        Preference order: configured override, project-local copy, installed
        package, official release download into the project.
        """
        self._module = self._try_import(prefer_local=True)
        if self._module is not None:
            self._version = self._module.version.__version__
            return True

        status("yt-dlp not found. Downloading official yt-dlp...")
        if not self._install_official(status):
            return False
        self._module = self._try_import()
        if self._module is None:
            status("Failed to load yt-dlp after installation.")
            return False
        self._version = self._module.version.__version__
        status(f"yt-dlp {self._version} installed.")
        return True

    def _try_import(self, prefer_local: bool = False) -> Any | None:
        """Attempt to import yt_dlp; returns None when unavailable.

        With ``prefer_local`` a project-local copy in ``bin/yt-dlp`` takes
        precedence over any globally installed package.
        """
        local_pkg = self._local_package_dir()
        if prefer_local and local_pkg is not None and str(local_pkg) not in sys.path:
            sys.path.insert(0, str(local_pkg))
        try:
            return importlib.import_module("yt_dlp")
        except ImportError:
            return None
        except Exception as exc:  # broken install, incompatible python, ...
            log_exception("yt_dlp import failed")
            print(f"yt-dlp import failed: {exc}")
            return None

    def _local_package_dir(self) -> Path | None:
        """Directory holding a project-local yt_dlp package, if any."""
        if YTDLP_DIR.joinpath("yt_dlp", "__init__.py").is_file():
            return YTDLP_DIR
        return None

    def _install_official(self, status: _STATUS) -> bool:
        """Download the official yt-dlp source release into ``bin/yt-dlp``."""
        YTDLP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            request = Request(_OFFICIAL_TARBALL, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=60) as response:
                payload = self._read_capped(response, _MAX_DOWNLOAD_BYTES)
            status("Extracting yt-dlp...")
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tf:
                self._extract_package(tf, YTDLP_DIR)
        except Exception as exc:
            log_exception("yt-dlp download failed")
            status(f"yt-dlp download failed: {exc}")
            shutil.rmtree(YTDLP_DIR, ignore_errors=True)
            return False
        return True

    @staticmethod
    def _read_capped(response: Any, limit: int) -> bytes:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise IOError("download exceeded size limit")
        return data

    @staticmethod
    def _extract_package(tf: tarfile.TarFile, destination: Path) -> None:
        """Extract only the ``yt_dlp`` package from the source tarball."""
        for member in tf.getmembers():
            parts = member.name.split("/")
            if len(parts) < 2 or parts[1] != "yt_dlp":
                continue
            target = destination.joinpath(*parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tf.extractfile(member)
            if source is None:
                continue
            with open(target, "wb") as handle:
                shutil.copyfileobj(source, handle)

    def update(self, status: _STATUS = print) -> bool:
        """Re-install the latest official yt-dlp into the project."""
        status(f"Updating yt-dlp (current: {self._version or 'unknown'})...")
        if self._install_official(status):
            self._purge_modules()
            self._module = self._try_import()
            if self._module is not None:
                self._version = self._module.version.__version__
                status(f"yt-dlp updated to {self._version}.")
                return True
            status("Updated on disk; restart the application to load it.")
            return True
        return False

    @staticmethod
    def _purge_modules() -> None:
        for name in [
            name
            for name in sys.modules
            if name == "yt_dlp" or name.startswith("yt_dlp.")
        ]:
            del sys.modules[name]


# --------------------------------------------------------------- error mapping
_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("sign in to confirm",
     "YouTube requires sign-in verification\n"
     "→ Set a cookies file in Settings and try again"),
    ("confirm your age",
     "Video is age-restricted\n"
     "→ Set a cookies file in Settings and try again"),
    ("private video", "Video is private"),
    ("members-only", "Video is members-only"),
    ("video unavailable", "Video is unavailable"),
    ("removed by the uploader", "Video is unavailable"),
    ("blocked it in your country", "Video is blocked in your country"),
    ("blocked it on copyright", "Video blocked by a copyright claim"),
    ("not a valid url", "Invalid YouTube URL"),
    ("unsupported url", "Unsupported URL for this downloader"),
    ("http error 429", "Rate limited by YouTube\n→ Wait a few minutes or set a proxy"),
    ("http error 403", "Access denied (HTTP 403)\n→ Try updating yt-dlp (menu option 7)"),
    ("requested format is not available",
     "Requested quality is not available\n→ Try another quality"),
    ("ffmpeg", "FFmpeg error\n→ Check FFmpeg installation (menu option 8)"),
    ("no video formats", "No downloadable formats found"),
    ("unable to download", "Download failed\n→ Check your network connection"),
    ("timed out", "Network connection lost\n→ Please try again"),
    ("connectionreset", "Network connection lost\n→ Please try again"),
    ("connection", "Network connection lost\n→ Please try again"),
    ("getaddrinfo failed", "Network error\n→ Check your internet connection"),
    ("temporary failure in name resolution", "Network error\n→ Check your internet connection"),
    ("ssl", "Network error (SSL)\n→ Check your connection or proxy settings"),
    ("cancelled by user", "Download cancelled"),
    ("does not exist", "File or path does not exist"),
    ("permission denied", "Permission denied\n→ Close players using the file or run from a writable folder"),
)


def friendly_error(exc: BaseException) -> str:
    """Convert a yt-dlp/OS exception into a short, readable message."""
    text = str(exc).strip()
    lowered = text.lower()
    for pattern, message in _ERROR_PATTERNS:
        if pattern in lowered:
            return message
    first_line = text.splitlines()[0] if text else exc.__class__.__name__
    return first_line[:300]


# ---------------------------------------------------------------- option build
def build_options(
    cfg: Config,
    ffmpeg_dir: Path | None,
    *,
    outdir: Path,
    outtmpl: str = "%(title)s.%(ext)s",
    format_selector: str | None = None,
    audio_extract: tuple[str, str] | None = None,
    progress_hooks: list[Any] | None = None,
    pp_hooks: list[Any] | None = None,
    overwrite: bool = False,
    resume: bool = True,
    noplaylist: bool = True,
    with_postprocessors: bool = True,
) -> dict[str, Any]:
    """Compose a yt-dlp option dictionary from application settings.

    ``audio_extract`` is ``(codec, quality)`` where quality is ``"128"``,
    ``"320"``, ... or ``"0"`` (best VBR) for FFmpegExtractAudio.
    """
    options: dict[str, Any] = {
        "paths": {"home": str(outdir)},
        "outtmpl": outtmpl,
        "format": format_selector or "bestvideo+bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "no_color": True,
        "logger": _YtDlpLogAdapter(),
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": resume,
        "overwrites": overwrite,
        "windowsfilenames": True,
        "noplaylist": noplaylist,
        "concurrent_fragment_downloads": max(1, int(cfg.concurrent_fragments)),
        "progress_hooks": list(progress_hooks or []),
        "postprocessor_hooks": list(pp_hooks or []),
        "ignoreerrors": False,
    }

    if ffmpeg_dir is not None:
        options["ffmpeg_location"] = str(ffmpeg_dir)

    proxy = cfg.proxy.strip()
    if proxy:
        options["proxy"] = proxy
    cookies = cfg.cookies_file.strip()
    if cookies:
        cookie_path = Path(cookies).expanduser()
        if cookie_path.is_file():
            options["cookiefile"] = str(cookie_path)
        else:
            log.warning("Configured cookies file not found: %s", cookie_path)

    postprocessors: list[dict[str, Any]] = []
    if with_postprocessors:
        if audio_extract is not None:
            codec, quality = audio_extract
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": codec,
                    "preferredquality": quality,
                }
            )
        if cfg.embed_metadata:
            postprocessors.append({"key": "FFmpegMetadata"})
        if cfg.embed_thumbnail:
            options["writethumbnail"] = True
            postprocessors.append({"key": "EmbedThumbnail"})
    if postprocessors:
        options["postprocessors"] = postprocessors

    if audio_extract is None:
        # Video downloads are merged into a single MP4 container.
        options["merge_output_format"] = "mp4"

    return options


ytdlp_manager = YtDlpManager()
