"""Download one public Suno URL through the bot pipeline for a deployment check."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from mutagen.mp3 import MP3

import bot


async def check(url: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(headers=bot.HEADERS, timeout=bot.REQUEST_TIMEOUT) as client:
        song_id = await bot.resolve_song_id(client, url)
        if not song_id:
            raise SystemExit("No Suno song UUID found in URL")
        path, metadata, cover = await bot.prepare_mp3(client, song_id, output_dir)

    audio = MP3(path)
    print(json.dumps({
        "ok": True,
        "path": str(path),
        "bytes": path.stat().st_size,
        "duration_seconds": round(audio.info.length, 2),
        "bitrate": audio.info.bitrate,
        "title": metadata["title"],
        "artist": metadata["artist"],
        "cover_bytes": len(cover or b""),
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/biradownbot-check"))
    args = parser.parse_args()
    asyncio.run(check(args.url, args.output_dir))


if __name__ == "__main__":
    main()
