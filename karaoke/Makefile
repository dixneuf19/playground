format:
	uv run ruff format
	uv run ruff check --fix

ESLINT = npm:eslint@10.14.0

check-format:
	uv run ruff format --check
	uv run ruff check
	deno run --quiet --allow-read --allow-env --allow-sys $(ESLINT) karaoke/static

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

LOCAL_DB = postgresql://postgres:karaoke@127.0.0.1:55432/postgres

db:
	docker start karaoke-db 2>/dev/null || docker run -d --name karaoke-db \
		-e POSTGRES_PASSWORD=karaoke -p 127.0.0.1:55432:5432 postgres:18-alpine

dev: db
	KARAOKE_DATA_DIR=./data KARAOKE_DATABASE_URL=$(LOCAL_DB) uv run karaoke

.PHONY: db dev
