# Radcast

Record a live audio stream on a timer and listen to it later as a podcast.

Point it at a station's stream URL, tell it which days and times to record, and it
captures those slots and serves them as a private podcast feed. Subscribe once in
Apple Podcasts, Overcast, Pocket Casts or AntennaPod and new episodes just appear,
with downloads, resume position and playback speed handled by the app you already use.

It is a VCR for internet radio. Nothing is scraped, no station is special-cased.

```
shows.yml                    the stream                 your phone
┌──────────────────────┐     ┌──────────┐              ┌───────────────┐
│ Breakfast Show       │     │ .m3u8 /  │   ffmpeg     │ podcast app   │
│ mon-fri 07:00-10:00  ├────▶│ Icecast  ├─────────────▶│ /feed.xml     │
│ The Late Session     │     └──────────┘   .m4a/.mp3  │ new episodes  │
│ fri,sat 23:00-01:00  │                     + RSS      └───────────────┘
└──────────────────────┘
```

## Quick start on a Raspberry Pi

A Pi 3 or newer running Raspberry Pi OS 64-bit (or any Linux box that is always on) is
all you need. Recording is a stream copy, so even a Pi barely notices.

**1. Install Docker and Tailscale**

```bash
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
```

Log out and in again so the docker group applies. Install Tailscale on your phone too
and sign in to the same account. In the Tailscale admin console, under DNS, enable
MagicDNS and HTTPS certificates (one-time).

**2. Get the three files**

```bash
mkdir -p ~/radcast/config ~/radcast/recordings && cd ~/radcast
curl -fsSLO https://raw.githubusercontent.com/willfthompson/radcast/main/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/willfthompson/radcast/main/.env.example -o .env
curl -fsSL  https://raw.githubusercontent.com/willfthompson/radcast/main/shows.example.yml -o config/shows.yml
```

**3. Fill them in**

Edit `.env`: set `STREAM_URL`, `TZ`, a username and password, and `FEED_URL` to your
Pi's Tailscale address, which looks like `https://raspberrypi.tail1234.ts.net`
(`tailscale status` shows the machine name; the tailnet name is in the admin console).

Edit `config/shows.yml` with your timers. Optionally drop a square JPEG at
`config/artwork.jpg` for cover art.

**4. Start it, and put HTTPS in front**

```bash
docker compose up -d
sudo tailscale serve --bg 5000
```

Tailscale Serve gives the Pi a real certificate and forwards the HTTPS address to
Radcast. Nothing is opened to the internet; only devices on your tailnet can reach it.

**5. Check it works**

Open `FEED_URL` in a browser on your phone or laptop. The dashboard shows whether the
stream was reachable and what codec it found, when each timer fires next, and the feed
URL to paste into your podcast app. Press **Record a 60-second test** and a test episode
appears in the feed, so you can confirm playback on your phone without waiting for the
first real show.

## Other ways to host it

**A cheap VPS with your own hostname.** Any 2 GB / 20 GB machine is plenty. Point a
DNS record at it (your own domain, or a free one from DuckDNS), set `DOMAIN` in `.env`,
set `FEED_URL=https://that-hostname`, and start with the bundled Caddy, which fetches
and renews the certificate itself:

```bash
docker compose --profile caddy up -d
```

If you already run a reverse proxy (Traefik, Nginx Proxy Manager, a Cloudflare Tunnel),
skip the profile and point your proxy at port 5000 on localhost.

**A PaaS such as Fly.io or Railway.** They give you an HTTPS subdomain, so no proxy is
needed. Pick a plan that never sleeps and attach a persistent volume at
`/data/recordings`. Mounting a file is awkward on these hosts, so put your shows in the
`SHOWS_JSON` variable instead of a file, as a JSON array of the same objects shown in
`shows.example.yml`.

**Without Docker.** `pipx install git+https://github.com/willfthompson/radcast` plus a
system `ffmpeg`. A sample systemd unit with instructions is in `contrib/radcast.service`.

Disk: twenty three-hour episodes is roughly 9 GB when copying a 320 kbps stream, or
3.5 GB at 128 kbps. Oldest episodes are deleted automatically beyond `MAX_EPISODES`.

## Configuration

Infrastructure lives in `.env`; the timers live in `config/shows.yml`. Both files are
commented, and `.env.example` documents every variable. The essentials:

| Variable | Required | What it is |
|---|---|---|
| `STREAM_URL` | yes | The stream to record. Use the URL of one specific bitrate variant, not a master playlist. |
| `TZ` | yes | Timezone the timers are written in, e.g. `Europe/Amsterdam`. |
| `FEED_URL` | yes | Public address podcast apps use, e.g. `https://raspberrypi.tail1234.ts.net`. |
| `FEED_USERNAME` / `FEED_PASSWORD` | recommended | Basic auth on everything except `/health` and artwork. |
| `FEED_TITLE`, `FEED_DESCRIPTION`, `FEED_AUTHOR`, `FEED_LANGUAGE`, `FEED_CATEGORY`, `FEED_EMAIL` | no | Feed metadata. |
| `ARTWORK` | no | Cover image path inside the container, default `/config/artwork.jpg`. |
| `AUDIO_MODE` | no | `auto` (default), `copy` or `transcode`. See below. |
| `TRANSCODE_BITRATE` | no | Default `128k`. |
| `MAX_EPISODES` | no | Default 20. |
| `START_BUFFER` / `END_BUFFER` | no | Seconds of slack around each timer, default 30 and 60. |
| `DOMAIN` | VPS only | Hostname for the optional Caddy service. |
| `PUID` / `PGID` | no | User the container runs as, default 1000. Must own `./recordings`. |

A timer:

```yaml
shows:
  - name: Breakfast Show        # becomes the episode title and the per-show feed
    days: mon-fri               # or sat,sun / daily / weekdays / weekends / [mon, wed]
    start: "07:00"
    end: "10:00"                # end earlier than start runs past midnight

  - name: Election Night Special
    on: 2026-11-03              # a one-off instead of days
    start: "21:00"
    end: "02:00"
    stream: https://other-station.example/live.m3u8   # optional, per show
    title: "{name}, {date:%A %-d %B}"                 # optional, Python strftime codes
    description: What it says on the tin.             # optional, for this show's feed
```

Changes to `shows.yml` take effect on `docker compose restart radcast`. Changes to
`.env` need `docker compose up -d` (a plain restart keeps the old environment).

## Adding the feed to your podcast app

The dashboard lists one combined feed and one feed per show, with your credentials
already in the URL. Copy the one you want.

- **Apple Podcasts:** Library, then the menu in the top right, then *Follow a Show by
  URL*. Paste the URL. It must be HTTPS with a valid certificate.
- **Overcast:** *Add URL* from the + menu.
- **Pocket Casts:** search bar, paste the URL.
- **AntennaPod:** *Add podcast by RSS address*.

Podcast apps cache cover art by URL. Radcast puts a hash of the image in the artwork URL,
so replacing `config/artwork.jpg` makes apps fetch the new one on their next refresh.

## How recording works

**Audio.** At startup, and again before every recording, Radcast asks ffprobe what the
stream is. MP3 and AAC-LC are copied as-is into `.mp3` or `.m4a` files: broadcast
quality, no CPU. Anything else is transcoded to AAC-LC, including HE-AAC, which starts
silent in some podcast apps when copied directly. The dashboard shows what was found
and which path will be used. `AUDIO_MODE=copy` copies any AAC regardless, and
`AUDIO_MODE=transcode` always re-encodes.

**Timing.** Each timer gets one job at `start - START_BUFFER`. It records until
`end + END_BUFFER`, so a show that runs a minute over is still complete. If Radcast
starts up, or restarts, while a show is on air, it records the rest of it. If a capture
fails early on (stream down, network blip), it retries, and a show that failed still has
its slot rebooked by an hourly replan while it is on air.

**Files.** Recordings are written to a `.part` file and renamed when complete, so the
recordings folder only ever holds finished, playable files. Each airing has a stable key
of show name plus local date, used for the filename and the feed GUID. The same airing is
never recorded twice, and changing `FEED_URL` doesn't make apps re-download everything.

## Commands

Run inside the container with `docker compose exec radcast <command>`:

```
radcast check          validate .env and shows.yml, list what was understood
radcast probe [URL]    inspect the stream and say how it would be recorded
radcast record [SECS]  make a short test recording to a local file
radcast plan [--days]  print the next week of airings
```

Endpoints: `/` dashboard, `/feed.xml` all shows, `/feeds/<slug>.xml` one show,
`/recordings/<file>`, `/logs`, and `/health` (no auth).

## Updating

```bash
docker compose pull && docker compose up -d
```

This restarts the container, which interrupts a recording in progress; check the
dashboard first.

## Troubleshooting

- **Dashboard says the stream is unreachable.** Run `docker compose exec radcast radcast
  probe` for the exact ffprobe error. Stations change stream URLs now and then; find the
  current one and update `STREAM_URL`, then `docker compose up -d`.
- **Container keeps restarting.** `docker compose logs radcast` prints exactly which
  setting is missing or malformed. The most common one is `./recordings` not being
  writable by the container user; the log tells you the `chown` to run.
- **Recording is shorter than the show.** The stream dropped and ffmpeg reached the end
  early. The file is kept as-is and the log says how long it got.
- **Apple Podcasts says it can't retrieve the audio.** Almost always a certificate
  problem. Check `FEED_URL` is HTTPS and opens without warnings in Safari on the phone.
- **Timer fires at the wrong hour.** `TZ` is wrong or missing. Times in `shows.yml` are
  always read in `TZ`.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest
```

The GitHub Actions workflow runs the tests and publishes a multi-arch image (amd64 and
arm64) to GHCR on every push to `main`.

## A note on recording

Radcast is a timer and a tape deck. Record only what you are permitted to record, keep
recordings for personal use, and don't redistribute them.

## Licence

MIT.
