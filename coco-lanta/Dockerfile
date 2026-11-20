FROM ghcr.io/astral-sh/uv:python3.15-trixie-slim@sha256:980d6aa868431fd4ef2b32481e934ef5ee3b662c5decade3aa2d7e7ffb8075e4 AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

ADD . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

# Links the ghcr.io package to the repository (lets Actions push to it).
LABEL org.opencontainers.image.source=https://github.com/dixneuf19/playground

RUN useradd --uid 1000 --create-home coco

COPY --from=builder /app/.venv /app/.venv
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8000

EXPOSE 8000

USER 1000
CMD ["coco-lanta"]
