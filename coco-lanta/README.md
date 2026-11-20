# coco-lanta

Live TV for a Koh-Lanta themed party ("Coco Lanta": Rouge Faucille against Rouge Marteau). Phones film the trials, the living room TV shows them like a TV show.

| Page | Who | What |
|---|---|---|
| `/` | guests (the one QR code) | Emoji and comments shown on the TV. Whoever wants to film picks a team and the phone becomes a camera, with a red frame when it is on air |
| `/tv` | laptop on the TV | The program: video, names, scores, timer, banner, reactions |
| `/regie` | the host | Pick the camera or leave it on auto, layouts (solo, duel, mosaic), scores, timer, banners, full-screen announcements |
| `/affiche` | printing | Poster with the QR code |

## How it works

- Video goes through a [LiveKit](https://github.com/livekit/livekit) server: phones publish to it, the TV and the control room subscribe. This app only signs the LiveKit tokens (`tokens.py`) and serves the pages.
- The show itself (cameras, what is on air, scores, timer) is in memory in `show.py`, pushed to every page over one WebSocket (`hub.py`). One replica, and a restart forgets scores and timer.
- Auto mode rotates through the cameras every 20 s and cuts to a camera that gets loud. The TV page reports LiveKit's active speakers, the server decides.
- Only the control room has a password (`COCO_REGIE_PASSWORD`). Anyone with the QR code can be a camera, and the control room can cut one.

## Settings

| Variable | Meaning |
|---|---|
| `COCO_PUBLIC_URL` | Where phones reach the app (the QR codes point there). Cameras need https |
| `COCO_LIVEKIT_URL` | The LiveKit server as browsers reach it (`wss://...`) |
| `COCO_LIVEKIT_API_KEY`, `COCO_LIVEKIT_API_SECRET` | A key pair of that server |
| `COCO_REGIE_PASSWORD` | Control room password (empty: open) |

## Development

```bash
uv sync
make livekit        # LiveKit in dev mode (nix run nixpkgs#livekit -- --dev)
make dev            # the app on :8000, against that server
make format         # Format + lint fix
make check-format   # Check formatting + linting (ruff, eslint through deno)
make typecheck      # Type check with ty
make test           # Unit tests
make sri            # Recompute the CDN integrity hashes after a version bump
```

Browsers only give the camera to https pages or `localhost`. For a run without phones, Chromium has fake cameras: `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream`.

## Look

A beach by day: sand, jungle green, lagoon, wooden planks (`static/app.css`, the logo is `templates/_logo.html`, the sickle and hammer icons are in `static/common.js`). The waiting screen photo (`static/idle.jpg`) is "Mu Ko Lanta NP (26)" by Krzysztof Golik, from Wikimedia Commons, licensed CC BY-SA 4.0.
