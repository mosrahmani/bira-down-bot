"""Download one supported social URL without requiring a Telegram token."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import bot


async def main() -> None:
    if len(sys.argv) != 2 or bot.classify_url(sys.argv[1]) not in {"youtube", "instagram"}:
        raise SystemExit("usage: python scripts/check_video.py <youtube-or-instagram-url>")
    with tempfile.TemporaryDirectory(prefix="biradownbot-video-check-") as temp_dir:
        path, info = await bot.prepare_video(sys.argv[1], Path(temp_dir))
        print(json.dumps({
            "ok": True,
            "service": bot.classify_url(sys.argv[1]),
            "id": info.get("id"),
            "title": info.get("title"),
            "duration_seconds": info.get("duration"),
            "bytes": path.stat().st_size,
            "extension": path.suffix,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
