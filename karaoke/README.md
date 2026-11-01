# karaoke

Self-hosted karaoke night, in the spirit of KaraFun bars and PiKaraoke:

- the **big screen** (a computer plugged into the TV/projector) creates a room and
  shows a QR code;
- **phones** scan it, search a song, and add it to the shared queue — reorder by
  drag and drop, skip, pause, change the volume;
- songs are searched in KaraFun's official YouTube channels first
  (`@karafunfr`, `@karafun`: clean "Karaoke Version" videos), and all of YouTube
  only after an explicit confirmation;
- videos are downloaded with yt-dlp (720p H.264) as soon as they're queued and
  cached on disk, the oldest evicted past a size limit.

## Run locally

```bash
uv sync
make dev            # http://localhost:8000
```

Open `http://<your-lan-ip>:8000` on the computer driving the TV, click
**Créer la salle**, then **▶ Lancer la soirée** (browsers need one click before
playing sound) and press **F** for full screen. Phones on the same network scan
the QR code.

Display shortcuts: `F` full screen, `Space` play/pause, `N` or `→` next song.

## Configuration

| Variable | Default | |
|---|---|---|
| `KARAOKE_DATA_DIR` | `./data` | Video cache (`songs/`) |
| `KARAOKE_HOST_PASSWORD` | _(empty)_ | Required to create a room. Guests never need it. Empty: anyone can. |
| `KARAOKE_PUBLIC_URL` | _(from request)_ | Base URL encoded in the QR code, e.g. `https://karaoke.example.com` |
| `KARAOKE_CHANNELS` | `karafunfr,karafun` | Curated YouTube channel handles, searched in parallel |
| `KARAOKE_CACHE_MAX_GB` | `20` | Cache size before evicting least recently played videos |
| `KARAOKE_MAX_HEIGHT` | `720` | Max video height downloaded |
| `PORT` | `8000` | |

## How it works

- `karaoke/app.py` — FastAPI app: pages, REST (`/api/...`), one WebSocket per
  screen/phone (`/ws/{code}`), and the download pipeline.
- `karaoke/rooms.py` — pure room state: queue, current song, player state.
  Rooms live in memory (24 h idle TTL): a restart empties the queues, not the
  video cache.
- `karaoke/search.py` — channel search + re-ranking (YouTube's in-channel search
  ranks loosely), title parsing of KaraFun's two naming schemes.
- `karaoke/library.py` — yt-dlp download to `<video_id>.mp4`, LRU eviction.
- `karaoke/static/` — no build step: plain HTML/CSS/JS. `display.html` is the
  big screen, `guest.html` the phone app.

The screen is the player: it reports `ended` and the server moves to the next
*ready* song (a song still downloading keeps its place). Skip, pause and volume
from phones go through the server to every screen in the room.

## Tests

```bash
make test                           # unit tests
uv run pytest -m integration -v     # real YouTube search + download
```
