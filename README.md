# BiraDownBot

A Telegram bot that automatically detects supported links:

- Public Suno song -> tagged MP3 with title, creator, and cover art
- Public YouTube video/Short -> MP4 video
- Public Instagram post/Reel/video -> MP4 video

Suno does not always expose the old `cdn1.suno.ai/<id>.mp3` download anymore.
The bot first tries that path; when it is unavailable, it reads the current
public media URL from the song page, requests Suno's anonymous playback rights,
decrypts the public playback stream, and uses FFmpeg to convert M4A/Opus to MP3.
YouTube and Instagram downloads use yt-dlp and FFmpeg, are limited to one item
per URL (never a playlist), prefer MP4 up to 720p, and are rejected if the final
file exceeds Telegram's bot upload limit.

## Requirements

- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A server that can reach Telegram, Suno, YouTube, and Instagram
- Docker Engine + Docker Compose (recommended), or Python 3.10+ and FFmpeg

## Create the Telegram bot

1. Open Telegram and start a chat with **@BotFather**.
2. Send `/newbot`, choose a name, and choose a username ending in `bot`.
3. Copy the token. Never commit or share it.
4. Optionally configure `/setdescription`, `/setabouttext`, and `/setuserpic`.

## Deploy with Docker Compose (recommended)

On a Linux VPS:

```bash
git clone YOUR_REPOSITORY_URL biradownbot
cd biradownbot
cp .env.example .env
nano .env
docker compose up -d --build
docker compose logs -f biradownbot
```

Set at least this value in `.env`:

```dotenv
BOT_TOKEN=123456789:replace_with_your_real_token
```

The image installs FFmpeg, runs as a non-root user, uses a read-only filesystem,
and gives downloads a temporary in-memory `/tmp`. No port or reverse proxy is
needed because the bot uses Telegram long polling.

Useful deployment commands:

```bash
# Show container state
docker compose ps

# Restart after changing only .env
docker compose up -d --force-recreate

# Rebuild after pulling source changes
git pull --ff-only
docker compose up -d --build

# Follow logs
docker compose logs -f --tail=200 biradownbot

# Stop without deleting the image
docker compose down
```

Only one running instance may poll Telegram with the same bot token. Stop an old
deployment before starting the replacement, otherwise Telegram reports a
`Conflict` error.

## Run without Docker

Install FFmpeg first (`apt install ffmpeg` on Debian/Ubuntu or `brew install
ffmpeg` on macOS), then:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a
. ./.env
set +a
python bot.py
```

## systemd deployment without Docker

Copy `bot.py`, `requirements.txt`, and your `.env` to `/opt/biradownbot`, create a
virtual environment, and install FFmpeg:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv ffmpeg
sudo mkdir -p /opt/biradownbot
sudo cp bot.py requirements.txt .env /opt/biradownbot/
cd /opt/biradownbot
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
```

Create `/etc/systemd/system/biradownbot.service`:

```ini
[Unit]
Description=BiraDownBot Telegram downloader
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=nobody
WorkingDirectory=/opt/biradownbot
EnvironmentFile=/opt/biradownbot/.env
ExecStart=/opt/biradownbot/.venv/bin/python /opt/biradownbot/bot.py
Restart=on-failure
RestartSec=5
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now biradownbot
sudo journalctl -u biradownbot -f
```

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `BOT_TOKEN` | yes | - | Telegram token from @BotFather |
| `LOG_LEVEL` | no | `INFO` | Python log level |
| `REQUEST_TIMEOUT` | no | `60` | HTTP and Telegram upload timeout in seconds |
| `MAX_CONCURRENT_DOWNLOADS` | no | `4` | Maximum concurrent user jobs |
| `MAX_LINKS_PER_MESSAGE` | no | `3` | Maximum links handled from one message |
| `AUDIO_BITRATE` | no | `192k` | MP3 bitrate used for M4A/Opus fallback |
| `VIDEO_MAX_HEIGHT` | no | `720` | Maximum preferred YouTube/Instagram video height |
| `YTDLP_COOKIES_FILE` | no | empty | Netscape cookie file for media that legitimately requires your account |

### Optional cookies for restricted media

Public links normally need no account. Some Instagram posts and YouTube videos
are login-, age-, or region-restricted. If you are authorized to access them,
export browser cookies in Netscape format to `cookies.txt`, keep that file out
of Git, uncomment `YTDLP_COOKIES_FILE` in `.env`, and add this read-only mount to
the `biradownbot` service in `docker-compose.yml`:

```yaml
volumes:
  - ./cookies.txt:/run/secrets/cookies.txt:ro
```

Cookies are account credentials: restrict file permissions, never commit them,
and refresh them when the provider expires the session.

## Verify before deployment

Run the offline unit tests and validate the Compose file:

```bash
python -m unittest discover -s tests -v
cp .env.example .env
docker compose config --quiet
```

For a real end-to-end check, start the container, send `/start`, then paste a
public Suno, YouTube, or Instagram URL. Suno should return an MP3; YouTube and
Instagram should return a streamable MP4.

You can also test Suno download/conversion without a Telegram token:

```bash
docker build -t biradownbot:test .
docker run --rm \
  -v "$PWD:/src:ro" \
  -w /src \
  -e PYTHONPATH=/src \
  biradownbot:test \
  python scripts/check_song.py "https://suno.com/song/<uuid>"
```

Test a public YouTube or Instagram URL without a Telegram token:

```bash
docker run --rm \
  -v "$PWD:/src:ro" \
  -w /src \
  -e PYTHONPATH=/src \
  biradownbot:test \
  python scripts/check_video.py "https://www.youtube.com/watch?v=<id>"
```

## Troubleshooting

- **`BOT_TOKEN is required`:** create `.env` from `.env.example` and set a real
  token.
- **Telegram `Conflict`:** another process/container is polling with the same
  token. Stop the other instance.
- **`Audio not found`:** the song is private, deleted, still generating, or no
  public media URL is present on its page.
- **`Video download failed`:** the post is private, deleted, login/age/region
  restricted, or the provider changed its site. Rebuild regularly to pick up a
  current yt-dlp release; use cookies only for content your account may access.
- **Generic creator/title:** Suno changed or withheld public page metadata. The
  audio can still be returned with fallback labels.
- **`ffmpeg is required`:** use the Docker image, or install FFmpeg on the host.
- **Network timeouts:** deploy outside a network that blocks Telegram or Suno;
  increase `REQUEST_TIMEOUT` for slow links.
- **Files above 50 MB:** Telegram's standard Bot API cannot accept them. The bot
  rejects them before upload.

The bot processes temporary media under `/tmp` and removes it after each job.
Only download media you own or are authorized to download, and follow each
platform's terms and the creator's rights.
# bira-down-bot
