"""Telegram bot that downloads Suno audio and public social videos."""

import asyncio
import base64
import hashlib
import html
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yt_dlp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from mutagen.id3 import APIC, COMM, TALB, TIT2, TPE1, ID3, ID3NoHeaderError
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
log = logging.getLogger("biradownbot")

UUID = r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"
UUID_RE = re.compile(UUID)
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
}
TG_LIMIT = 49 * 1024 * 1024
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "60"))
MAX_LINKS = int(os.getenv("MAX_LINKS_PER_MESSAGE", "3"))
AUDIO_BITRATE = os.getenv("AUDIO_BITRATE", "192k")
VIDEO_MAX_HEIGHT = max(144, int(os.getenv("VIDEO_MAX_HEIGHT", "720")))
YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()
TELEGRAM_RELAY_URL = os.getenv("TELEGRAM_RELAY_URL", "").strip().rstrip("/")
TELEGRAM_RELAY_SECRET = os.getenv("TELEGRAM_RELAY_SECRET", "").strip()
TELEGRAM_POLL_TIMEOUT = max(0, int(os.getenv("TELEGRAM_POLL_TIMEOUT", "3")))
TELEGRAM_POLL_INTERVAL = max(0.0, float(os.getenv("TELEGRAM_POLL_INTERVAL", "0.25")))
SUNO_API_BASE = "https://studio-api-prod.suno.com"
SEM = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "4")))


class DownloadTooLarge(Exception):
    """Raised when a remote or converted file cannot be uploaded to Telegram."""


class VideoDownloadError(Exception):
    """Raised when yt-dlp cannot produce an uploadable public video."""


def classify_url(url: str) -> str | None:
    """Classify supported URLs using exact hosts to avoid arbitrary downloads."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host in {"suno.com", "www.suno.com", "suno.ai", "www.suno.ai", "app.suno.ai"}:
        return "suno"
    if host in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
        "youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
    }:
        return "youtube"
    if host in {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am"}:
        return "instagram"
    return None


def extract_supported_urls(text: str) -> list[tuple[str, str]]:
    """Return normalized supported URLs and their media kind."""
    found: list[tuple[str, str]] = []
    for raw_url in URL_RE.findall(text):
        url = raw_url.rstrip(".,;:!?)]}>\"'")
        kind = classify_url(url)
        if kind:
            found.append((url, kind))
    return found


def _decoded_page(text: str) -> str:
    """Make JSON strings embedded in Next.js/RSC HTML searchable."""
    return html.unescape(text).replace(r'\"', '"').replace(r"\u0026", "&")


def parse_song_page(text: str, song_id: str) -> dict:
    """Extract best-effort metadata and current public media URLs from a Suno page."""
    page = _decoded_page(text)
    info = {
        "title": f"Suno {song_id[:8]}",
        "artist": "Suno AI",
        "cover_url": f"https://cdn2.suno.ai/image_large_{song_id}.jpeg",
        "media_url": None,
        "media_encrypted": False,
    }

    title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', page)
    if title:
        info["title"] = title.group(1).replace(" | Suno", "").strip()

    # Avoid unrelated display_name values such as the model/version badge.
    artist = re.search(r'"user_id":"[^"]+".{0,600}?"display_name":"([^"]+)"', page)
    if artist:
        info["artist"] = artist.group(1).strip()

    cover = re.search(r'"image_large_url":"(https?://[^"]+)"', page)
    if cover:
        info["cover_url"] = cover.group(1)

    media_block = re.search(r'"media_urls":\[(.{0,2000}?)\]', page, re.DOTALL)
    if media_block:
        candidates = re.findall(r'"url":"(https?://[^"]+)"', media_block.group(1))
        info["media_url"] = next((url for url in candidates if _allowed_media_url(url)), None)
        info["media_encrypted"] = '"encoding":' in media_block.group(1)
    return info


def _allowed_media_url(url: str) -> bool:
    """Only download media hosts referenced by Suno, never arbitrary page URLs."""
    host = (urlparse(url).hostname or "").lower()
    return host.endswith(".suno.ai") or host.endswith(".suno.com") or host.endswith(".cloudfront.net")


async def resolve_song_id(client: httpx.AsyncClient, url: str) -> str | None:
    """Return the UUID from a song URL or by following a Suno short link."""
    match = UUID_RE.search(url)
    if match:
        return match.group(0).lower()
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()
    match = UUID_RE.search(str(response.url)) or UUID_RE.search(response.text)
    return match.group(0).lower() if match else None


async def fetch_song_info(client: httpx.AsyncClient, song_id: str) -> dict:
    """Fetch the public page once for metadata and Suno's current media URL."""
    response = await client.get(f"https://suno.com/song/{song_id}", follow_redirects=True)
    response.raise_for_status()
    return parse_song_page(response.text, song_id)


async def fetch_bytes(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        response = await client.get(url, follow_redirects=True)
        if response.status_code != 200:
            return None
        return response.content
    except httpx.HTTPError:
        return None


async def download_file(client: httpx.AsyncClient, url: str, path: Path) -> bool:
    """Stream a remote file to disk with Telegram's size limit enforced."""
    async with client.stream("GET", url, follow_redirects=True) as response:
        if response.status_code != 200:
            return False
        declared_size = int(response.headers.get("content-length", "0") or 0)
        if declared_size > TG_LIMIT:
            raise DownloadTooLarge
        size = 0
        with path.open("wb") as output:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > TG_LIMIT:
                    raise DownloadTooLarge
                output.write(chunk)
    return size > 0


def _unwrap_suno_secret(value: str, song_id: str, guest_license_token: str) -> bytes:
    """Unwrap a Suno media key/IV using the public guest playback license."""
    wrapped = base64.b64decode(value)
    wrapping_key = hashlib.sha256(guest_license_token.encode()).digest()
    return AESGCM(wrapping_key).decrypt(wrapped[:12], wrapped[12:], song_id.encode())


async def fetch_media_rights(client: httpx.AsyncClient, song_id: str) -> tuple[bytes, bytes]:
    response = await client.post(
        f"{SUNO_API_BASE}/api/mango/rights",
        json={"content_params": {"content_id": song_id, "content_type": "clip"}},
    )
    response.raise_for_status()
    rights = response.json()
    guest_token = rights.get("glt")
    if not all(rights.get(field) for field in ("key", "iv")) or not guest_token:
        raise RuntimeError("Suno playback license is incomplete")
    return (
        _unwrap_suno_secret(rights["key"], song_id, guest_token),
        _unwrap_suno_secret(rights["iv"], song_id, guest_token),
    )


async def download_encrypted_file(client: httpx.AsyncClient, url: str, path: Path, song_id: str) -> bool:
    """Download and AES-CTR decrypt Suno's current guest-playback media."""
    (key, iv), response = await asyncio.gather(
        fetch_media_rights(client, song_id),
        client.send(client.build_request("GET", url), stream=True),
    )
    try:
        if response.status_code != 200:
            return False
        declared_size = int(response.headers.get("content-length", "0") or 0)
        if declared_size > TG_LIMIT:
            raise DownloadTooLarge
        decryptor = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
        size = 0
        with path.open("wb") as output:
            async for chunk in response.aiter_bytes():
                clear = decryptor.update(chunk)
                size += len(clear)
                if size > TG_LIMIT:
                    raise DownloadTooLarge
                output.write(clear)
            output.write(decryptor.finalize())
        return size > 0
    finally:
        await response.aclose()


async def convert_to_mp3(source: Path, destination: Path) -> None:
    """Convert Suno's current M4A/Opus delivery format to a Telegram MP3."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for Suno's current media format")
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-vn", "-codec:a", "libmp3lame", "-b:a", AUDIO_BITRATE,
        str(destination),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace')[-500:]}")
    if destination.stat().st_size > TG_LIMIT:
        raise DownloadTooLarge


def tag_mp3(path: str | Path, meta: dict, cover: bytes | None, song_id: str) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for frame in ("TIT2", "TPE1", "TALB", "COMM", "APIC"):
        tags.delall(frame)
    tags.add(TIT2(encoding=3, text=meta["title"]))
    tags.add(TPE1(encoding=3, text=meta["artist"]))
    tags.add(TALB(encoding=3, text="Suno AI"))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=f"https://suno.com/song/{song_id}"))
    if cover:
        mime = "image/png" if cover.startswith(b"\x89PNG") else "image/jpeg"
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
    tags.save(path, v2_version=3)


async def prepare_mp3(client: httpx.AsyncClient, song_id: str, directory: Path) -> tuple[Path, dict, bytes | None]:
    """Download a direct MP3 when available, otherwise convert Suno's media URL."""
    info = await fetch_song_info(client, song_id)
    cover_task = asyncio.create_task(fetch_bytes(client, info["cover_url"]))
    mp3_path = directory / f"{song_id}.mp3"

    direct_url = f"https://cdn1.suno.ai/{song_id}.mp3"
    if not await download_file(client, direct_url, mp3_path):
        media_url = info.get("media_url")
        if not media_url:
            await cover_task
            raise FileNotFoundError("Suno did not expose downloadable audio")
        source_path = directory / "source.m4a"
        if info.get("media_encrypted"):
            downloaded = await download_encrypted_file(client, media_url, source_path, song_id)
        else:
            downloaded = await download_file(client, media_url, source_path)
        if not downloaded:
            await cover_task
            raise FileNotFoundError("Suno media could not be downloaded")
        await convert_to_mp3(source_path, mp3_path)

    cover = await cover_task
    tag_mp3(mp3_path, info, cover, song_id)
    return mp3_path, info, cover


def _video_download_options(directory: Path) -> dict:
    """Build conservative yt-dlp options for one Telegram-sized MP4."""
    options = {
        "outtmpl": str(directory / "%(id)s.%(ext)s"),
        "format": (
            f"bv[height<={VIDEO_MAX_HEIGHT}][ext=mp4]+ba[ext=m4a]/"
            f"b[height<={VIDEO_MAX_HEIGHT}][ext=mp4]/best[height<={VIDEO_MAX_HEIGHT}]/"
            "best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "max_filesize": TG_LIMIT,
        "socket_timeout": REQUEST_TIMEOUT,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
    }
    if YTDLP_COOKIES_FILE:
        cookie_path = Path(YTDLP_COOKIES_FILE)
        if not cookie_path.is_file():
            raise VideoDownloadError(f"Cookie file does not exist: {cookie_path}")
        options["cookiefile"] = str(cookie_path)
    return options


def _download_video_sync(url: str, directory: Path) -> tuple[Path, dict]:
    """Run yt-dlp off the event loop and locate its final merged video."""
    try:
        with yt_dlp.YoutubeDL(_video_download_options(directory)) as downloader:
            info = downloader.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise VideoDownloadError(str(exc)) from exc

    candidates = [
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp4" and not path.name.endswith(".part")
    ]
    if not candidates:
        raise VideoDownloadError("Downloader did not produce an MP4 file")
    path = max(candidates, key=lambda item: item.stat().st_size)
    if path.stat().st_size > TG_LIMIT:
        raise DownloadTooLarge
    return path, info


async def prepare_video(url: str, directory: Path) -> tuple[Path, dict]:
    """Download a single public YouTube or Instagram URL as an MP4."""
    return await asyncio.to_thread(_download_video_sync, url, directory)


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value).strip(" ._")[:80]
    return cleaned or fallback


def telegram_request(relay_secret: str = "") -> HTTPXRequest:
    """Create a Telegram HTTP client authenticated to the private relay."""
    httpx_kwargs = None
    if relay_secret:
        httpx_kwargs = {"headers": {"X-Bira-Relay-Secret": relay_secret}}
    return HTTPXRequest(
        connect_timeout=REQUEST_TIMEOUT,
        read_timeout=REQUEST_TIMEOUT,
        write_timeout=REQUEST_TIMEOUT,
        media_write_timeout=REQUEST_TIMEOUT,
        httpx_kwargs=httpx_kwargs,
    )


def relay_token(bot_id: str) -> str:
    """Build a non-secret placeholder token for python-telegram-bot URL generation."""
    if not bot_id.isdigit():
        raise ValueError("TELEGRAM_BOT_ID must be numeric")
    return f"{bot_id}:BiraDownBotRelayToken000000000000000"


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send a public link from Suno, YouTube, or Instagram. "
        "Suno links return a tagged MP3; YouTube and Instagram links return an MP4 video."
    )


async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    urls = extract_supported_urls(update.message.text or "")
    if not urls:
        await update.message.reply_text("Please send a valid Suno, YouTube, or Instagram link.")
        return

    status = await update.message.reply_text("Fetching…")
    try:
        async with SEM, httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT) as client:
            for url, kind in urls[:MAX_LINKS]:
                try:
                    with tempfile.TemporaryDirectory(prefix="biradownbot-") as temp_dir:
                        directory = Path(temp_dir)
                        if kind == "suno":
                            song_id = await resolve_song_id(client, url)
                            if not song_id:
                                await update.message.reply_text(f"Couldn't find a song ID in {url}")
                                continue
                            path, meta, _cover = await prepare_mp3(client, song_id, directory)
                            name = safe_filename(meta["title"], song_id)
                            with path.open("rb") as media_file:
                                await update.message.reply_audio(
                                    media_file, filename=f"{name}.mp3", title=meta["title"],
                                    performer=meta["artist"], read_timeout=REQUEST_TIMEOUT,
                                    write_timeout=REQUEST_TIMEOUT,
                                )
                        else:
                            path, meta = await prepare_video(url, directory)
                            fallback = str(meta.get("id") or kind)
                            name = safe_filename(str(meta.get("title") or "video"), fallback)
                            duration = meta.get("duration")
                            with path.open("rb") as media_file:
                                await update.message.reply_video(
                                    media_file, filename=f"{name}.mp4",
                                    caption=str(meta.get("title") or "")[:1024] or None,
                                    duration=int(duration) if duration else None,
                                    supports_streaming=True, read_timeout=REQUEST_TIMEOUT,
                                    write_timeout=REQUEST_TIMEOUT,
                                )
                except DownloadTooLarge:
                    await update.message.reply_text("File is too large for Telegram bots (50 MB).")
                except FileNotFoundError:
                    await update.message.reply_text("Audio not found (private, deleted or still generating).")
                except VideoDownloadError:
                    log.exception("video download failed")
                    await update.message.reply_text(
                        "Video download failed. The post may be private, login-protected, deleted, "
                        "or unavailable from this server."
                    )
                except Exception:
                    log.exception("download failed")
                    await update.message.reply_text("Something went wrong, try again later.")
    finally:
        await status.delete()


def main() -> None:
    bot_id = os.getenv("TELEGRAM_BOT_ID", "").strip()
    if not bot_id:
        raise SystemExit("TELEGRAM_BOT_ID is required; it is the numeric part before ':' in the bot token")
    if not TELEGRAM_RELAY_URL or not TELEGRAM_RELAY_SECRET:
        raise SystemExit("TELEGRAM_RELAY_URL and TELEGRAM_RELAY_SECRET are required")
    request = telegram_request(TELEGRAM_RELAY_SECRET)
    updates_request = telegram_request(TELEGRAM_RELAY_SECRET)
    builder = (
        Application.builder()
        .token(relay_token(bot_id))
        .base_url(f"{TELEGRAM_RELAY_URL}/api/bot")
        .base_file_url(f"{TELEGRAM_RELAY_URL}/file/bot")
        .request(request)
        .get_updates_request(updates_request)
    )
    app = builder.build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    log.info(
        "Starting Telegram polling through relay (timeout=%ss, interval=%ss)",
        TELEGRAM_POLL_TIMEOUT,
        TELEGRAM_POLL_INTERVAL,
    )
    app.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=-1,
        timeout=TELEGRAM_POLL_TIMEOUT,
        poll_interval=TELEGRAM_POLL_INTERVAL,
    )


if __name__ == "__main__":
    main()
