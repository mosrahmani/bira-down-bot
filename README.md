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
- A Cloudflare account for the private Telegram relay Worker
- A server that can reach the Worker, Suno, YouTube, and Instagram
- Docker Engine + Docker Compose (recommended), or Python 3.10+ and FFmpeg

## Create the Telegram bot

1. Open Telegram and start a chat with **@BotFather**.
2. Send `/newbot`, choose a name, and choose a username ending in `bot`.
3. Copy the token. Never commit or share it.
4. Optionally configure `/setdescription`, `/setabouttext`, and `/setuserpic`.

## Deploy the Cloudflare Telegram relay

The Worker is a private, authenticated relay; it is not an open proxy. The real
BotFather token is stored only as a Cloudflare secret and is never sent in a
request URL or stored on the application server.

```bash
cd cloudflare-worker
npm install
npx wrangler login
npx wrangler secret put BOT_TOKEN
```

Paste the full BotFather token when prompted. Generate a separate relay secret,
save it in your password manager, and add it to the Worker:

```bash
openssl rand -hex 32
npx wrangler secret put RELAY_SECRET
npm run deploy
```

Copy the deployed `https://...workers.dev` URL. An unauthenticated health check
must return `401`:

```bash
curl -i https://YOUR-WORKER.workers.dev/
```

Do not put either secret in `wrangler.jsonc`, Git, build arguments, or command
line URLs. Worker observability is disabled so Telegram subrequest URLs containing
the token are not retained in Worker logs. Cloudflare streams request and
response bodies; the Worker does not buffer uploaded MP3/MP4 files in memory.

## Deploy BiraDownBot with Docker Compose

On a Linux VPS:

```bash
git clone YOUR_REPOSITORY_URL biradownbot
cd biradownbot
cp .env.example .env
nano .env
docker compose up -d --build
docker compose logs -f biradownbot
```

Set these values in `.env`:

```dotenv
TELEGRAM_BOT_ID=123456789
TELEGRAM_RELAY_URL=https://YOUR-WORKER.workers.dev
TELEGRAM_RELAY_SECRET=the_same_random_relay_secret
```

`TELEGRAM_BOT_ID` is only the numeric part before `:` in the BotFather token.
The full token must not be copied to this server.

The image installs FFmpeg, runs as a non-root user, uses a read-only filesystem,
and gives downloads a temporary in-memory `/tmp`. No port or reverse proxy is
needed because the bot uses Telegram long polling.

The Dockerfile uses ArvanCloud's Iranian Docker Hub mirror by default to avoid
Docker Hub connectivity problems inside Iran. To use the official registry on
a server outside Iran, override the base image when building:

```bash
docker compose build --build-arg PYTHON_IMAGE=python:3.12-slim
docker compose up -d
```

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
| `TELEGRAM_BOT_ID` | yes | - | Numeric part before `:` in the BotFather token |
| `TELEGRAM_RELAY_URL` | yes | - | HTTPS URL of the deployed private Cloudflare Worker |
| `TELEGRAM_RELAY_SECRET` | yes | - | Random shared secret also stored in the Worker |
| `LOG_LEVEL` | no | `INFO` | Python log level |
| `REQUEST_TIMEOUT` | no | `60` | HTTP and Telegram upload timeout in seconds |
| `MAX_CONCURRENT_DOWNLOADS` | no | `4` | Maximum concurrent user jobs |
| `MAX_LINKS_PER_MESSAGE` | no | `3` | Maximum links handled from one message |
| `AUDIO_BITRATE` | no | `192k` | MP3 bitrate used for M4A/Opus fallback |
| `VIDEO_MAX_HEIGHT` | no | `720` | Maximum preferred YouTube/Instagram video height |
| `YTDLP_COOKIES_FILE` | no | empty | Netscape cookie file for media that legitimately requires your account |

### How the Telegram relay works

Both normal Bot API calls and long polling are sent to the Worker over HTTPS.
The application authenticates with `TELEGRAM_RELAY_SECRET` and uses a non-secret
placeholder token in request URLs. The Worker validates the secret, inserts the
real `BOT_TOKEN` from Cloudflare Secrets, forwards the request to Telegram, and
streams Telegram's response back to the bot.

The Worker accepts only authenticated `GET`/`POST` requests matching Telegram
API or file paths. It strips the relay secret and client forwarding headers
before contacting Telegram. BiraDownBot retries initialization indefinitely
instead of entering a container restart loop during a temporary outage.

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

- **Missing relay configuration:** create `.env` from `.env.example` and set the
  bot id, deployed Worker URL, and matching relay secret.
- **Telegram `Conflict`:** another process/container is polling with the same
  token. Stop the other instance.
- **Worker returns `401`:** `TELEGRAM_RELAY_SECRET` does not match the Worker's
  `RELAY_SECRET` secret.
- **`httpx.ConnectError: All connection attempts failed`:** verify the Worker URL
  is reachable from the server, then recreate the container.
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
