"""Private HTTP API used by the BiraDownBot Next.js web app."""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from bot import (
    HEADERS,
    REQUEST_TIMEOUT,
    DownloadTooLarge,
    classify_url,
    prepare_mp3,
    resolve_song_id,
    safe_filename,
)

log = logging.getLogger("biradownbot.web")
app = FastAPI(title="BiraDownBot Download API", docs_url=None, redoc_url=None)
WEB_SEM = asyncio.Semaphore(max(1, int(os.getenv("WEB_MAX_CONCURRENT_DOWNLOADS", "2"))))


class DownloadRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/download")
async def download(payload: DownloadRequest) -> FileResponse:
    url = payload.url.strip()
    if classify_url(url) != "suno":
        raise HTTPException(status_code=400, detail="Enter a valid public Suno song link.")

    temp_dir = Path(tempfile.mkdtemp(prefix="biradownbot-web-"))
    try:
        async with WEB_SEM, httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT) as client:
            song_id = await resolve_song_id(client, url)
            if not song_id:
                raise HTTPException(status_code=404, detail="We could not find a song in that link.")
            path, metadata, _cover = await prepare_mp3(client, song_id, temp_dir)

        filename = f"{safe_filename(metadata['title'], song_id)}.mp3"
        return FileResponse(
            path,
            media_type="audio/mpeg",
            filename=filename,
            headers={
                "Cache-Control": "no-store",
                "X-Song-Title": quote(str(metadata["title"]), safe=""),
                "X-Song-Artist": quote(str(metadata["artist"]), safe=""),
            },
            background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True),
        )
    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except DownloadTooLarge as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=413, detail="This audio file is too large to download.") from exc
    except FileNotFoundError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=404,
            detail="Audio is unavailable. The song may be private, deleted, or still generating.",
        ) from exc
    except httpx.HTTPError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        log.warning("Suno request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Suno did not respond. Please try again.") from exc
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        log.exception("Web download failed")
        raise HTTPException(status_code=500, detail="We could not prepare this song. Please try again.") from exc
