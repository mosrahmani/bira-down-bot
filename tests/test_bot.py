import tempfile
import unittest
import base64
import hashlib
from pathlib import Path

import httpx
from mutagen.id3 import ID3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import bot


SONG_ID = "96c1f74f-a0c7-4d98-b95f-74ceefd3051a"


class SongPageTests(unittest.TestCase):
    def test_parses_current_escaped_suno_payload(self):
        page = f'''<meta property="og:title" content="Test Song" />
        data=\"user_id\":\"abc\",\"display_name\":\"Test Artist\",\"handle\":\"artist\",
        \"media_urls\":[{{\"url\":\"https://media.example.cloudfront.net/clip/{SONG_ID}.m4a\",
        \"content_type\":\"m4a-opus\",\"encoding\":\"1.0.0\"}}],
        \"image_large_url\":\"https://cdn2.suno.ai/image_large_{SONG_ID}.jpeg\"'''

        result = bot.parse_song_page(page, SONG_ID)

        self.assertEqual(result["title"], "Test Song")
        self.assertEqual(result["artist"], "Test Artist")
        self.assertTrue(result["media_url"].endswith(".m4a"))
        self.assertTrue(result["media_encrypted"])
        self.assertTrue(result["cover_url"].endswith(".jpeg"))

    def test_rejects_arbitrary_media_host(self):
        page = r'\"media_urls\":[{\"url\":\"https://evil.example/audio.m4a\"}]'
        self.assertIsNone(bot.parse_song_page(page, SONG_ID)["media_url"])

    def test_tags_mp3(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.mp3"
            path.touch()
            bot.tag_mp3(path, {"title": "Title", "artist": "Artist"}, None, SONG_ID)
            tags = ID3(path)
            self.assertEqual(str(tags["TIT2"]), "Title")
            self.assertEqual(str(tags["TPE1"]), "Artist")

    def test_unwraps_guest_playback_secret(self):
        guest_token = "guest-license-token"
        secret = b"0123456789abcdef"
        nonce = b"123456789012"
        wrapping_key = hashlib.sha256(guest_token.encode()).digest()
        wrapped = nonce + AESGCM(wrapping_key).encrypt(nonce, secret, SONG_ID.encode())

        result = bot._unwrap_suno_secret(base64.b64encode(wrapped).decode(), SONG_ID, guest_token)

        self.assertEqual(result, secret)


class ResolveSongTests(unittest.IsolatedAsyncioTestCase):
    async def test_uuid_link_needs_no_network(self):
        async def fail_if_called(request):
            raise AssertionError("network should not be called")

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail_if_called)) as client:
            result = await bot.resolve_song_id(client, f"https://suno.com/song/{SONG_ID}")
        self.assertEqual(result, SONG_ID)

    async def test_short_link_follows_redirect(self):
        async def redirect(request):
            if "/s/" in request.url.path:
                return httpx.Response(302, headers={"location": f"https://suno.com/song/{SONG_ID}"})
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
            result = await bot.resolve_song_id(client, "https://suno.com/s/example")
        self.assertEqual(result, SONG_ID)


class LinkRoutingTests(unittest.TestCase):
    def test_classifies_supported_services(self):
        self.assertEqual(bot.classify_url("https://youtu.be/abc"), "youtube")
        self.assertEqual(bot.classify_url("https://www.youtube.com/shorts/abc"), "youtube")
        self.assertEqual(bot.classify_url("https://www.instagram.com/reel/abc/"), "instagram")
        self.assertEqual(bot.classify_url(f"https://suno.com/song/{SONG_ID}"), "suno")

    def test_rejects_lookalike_and_arbitrary_hosts(self):
        self.assertIsNone(bot.classify_url("https://youtube.com.evil.example/watch?v=abc"))
        self.assertIsNone(bot.classify_url("https://example.com/video.mp4"))

    def test_extracts_links_and_trims_message_punctuation(self):
        result = bot.extract_supported_urls(
            "Watch (https://youtu.be/abc), and https://instagram.com/reel/xyz/."
        )
        self.assertEqual(
            result,
            [("https://youtu.be/abc", "youtube"),
             ("https://instagram.com/reel/xyz/", "instagram")],
        )

    def test_video_options_disable_playlists_and_cap_height(self):
        with tempfile.TemporaryDirectory() as directory:
            options = bot._video_download_options(Path(directory))
        self.assertTrue(options["noplaylist"])
        self.assertIn("height<=", options["format"])
        self.assertEqual(options["max_filesize"], bot.TG_LIMIT)

    def test_safe_filename_removes_path_characters(self):
        self.assertEqual(bot.safe_filename('bad/name:*?"', "fallback"), "bad_name")

    def test_progress_labels_cover_download_upload_and_multiple_links(self):
        self.assertEqual(bot.progress_text("download", "suno", 1, 1), "⏳ Downloading audio…")
        self.assertEqual(bot.progress_text("upload", "youtube", 2, 3), "📤 Uploading video… (2/3)")

    def test_builds_authenticated_relay_request(self):
        request = bot.telegram_request("relay-secret")
        self.assertEqual(request._client.headers["x-bira-relay-secret"], "relay-secret")

    def test_relay_token_preserves_only_bot_id(self):
        result = bot.relay_token("123456")
        self.assertTrue(result.startswith("123456:"))

    def test_relay_token_rejects_invalid_bot_token(self):
        with self.assertRaises(ValueError):
            bot.relay_token("invalid")


if __name__ == "__main__":
    unittest.main()
