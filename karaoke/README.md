# karaoke

Self-hosted karaoke night, in the spirit of KaraFun bars. A computer on the TV
creates a room and shows a QR code; phones scan it to queue songs (KaraFun's
YouTube channels first, all of YouTube on request), control playback, send
photos to the big screen and make emoji, confetti and hearts fly across it.

Live at https://karaoke.dixneuf19.fr.

## Run locally

Needs Docker for the local Postgres.

```bash
uv sync
make dev            # Postgres on :55432 + app on http://localhost:8000
```

On the TV computer: **Créer la salle**, then **▶ Lancer la soirée** and `F` for
full screen. Phones on the same network scan the QR code.

Screen shortcuts: `F` full screen, `Space` play/pause, `N` or `→` next song,
`P` next photo.

## Tests

```bash
make check-format typecheck test    # unit tests (start a throwaway Postgres)
uv run pytest -m integration -v     # real YouTube search + download
```

Architecture, configuration and deployment: see [AGENTS.md](AGENTS.md).
