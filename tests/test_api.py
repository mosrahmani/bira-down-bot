import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

import api


SONG_ID = "96c1f74f-a0c7-4d98-b95f-74ceefd3051a"


class DownloadApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_health(self):
        response = await self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    async def test_rejects_non_suno_url(self):
        response = await self.client.post("/download", json={"url": "https://example.com/song"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Enter a valid public Suno song link.")

    async def test_returns_tagged_mp3_download(self):
        async def fake_prepare(_client, _song_id, directory: Path):
            path = directory / "song.mp3"
            path.write_bytes(b"fake-mp3")
            return path, {"title": "A Test Song", "artist": "A Test Artist"}, None

        with (
            patch.object(api, "resolve_song_id", AsyncMock(return_value=SONG_ID)),
            patch.object(api, "prepare_mp3", side_effect=fake_prepare),
        ):
            response = await self.client.post(
                "/download",
                json={"url": f"https://suno.com/song/{SONG_ID}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"fake-mp3")
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertIn("A%20Test%20Song.mp3", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-song-title"], "A%20Test%20Song")


if __name__ == "__main__":
    unittest.main()
