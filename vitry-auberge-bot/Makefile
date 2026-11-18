dev:
	uv run python -m vitry_auberge_bot.bot

sync-lafourche:
	uv run python -m vitry_auberge_bot.lafourche_history

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

.PHONY: dev sync-lafourche format check-format typecheck test
