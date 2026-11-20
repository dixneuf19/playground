format:
	uv run ruff format
	uv run ruff check --fix

ESLINT = npm:eslint@10.14.0

check-format:
	uv run ruff format --check
	uv run ruff check
	deno run --quiet --allow-read --allow-env --allow-sys $(ESLINT) coco_lanta/static

typecheck:
	uv run ty check

test:
	uv run pytest tests

.PHONY: format check-format typecheck test

# Integrity hashes of the CDN scripts in the templates (network needed)
sri:
	uv run python scripts/sri.py

check-sri:
	uv run python scripts/sri.py --check

.PHONY: sri check-sri

# A local LiveKit server in dev mode (key "devkey", secret "secret") + the app
# on :8000. Cameras need https or localhost.
livekit:
	livekit-server --dev --bind 0.0.0.0

dev:
	COCO_LIVEKIT_URL=ws://localhost:7880 COCO_LIVEKIT_API_KEY=devkey \
	COCO_LIVEKIT_API_SECRET=secret uv run coco-lanta

.PHONY: livekit dev
