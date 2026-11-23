format:
	uv run ruff format
	uv run ruff check --fix

check-format:
	uv run ruff format --check
	uv run ruff check

typecheck:
	uv run ty check

test:
	uv run pytest tests

.PHONY: format check-format typecheck test

LOCAL_DB = postgresql://postgres:xy@127.0.0.1:55433/postgres

db:
	docker start xy-db 2>/dev/null || docker run -d --name xy-db \
		-e POSTGRES_PASSWORD=xy -p 127.0.0.1:55433:5432 postgres:18-alpine

dev: db
	XY_DATABASE_URL=$(LOCAL_DB) uv run xy

.PHONY: db dev
