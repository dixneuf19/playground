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
