FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

ADD . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm@sha256:061b6e52a07ab675f0e4a9428c5a8ee6bed996983427f4691f6bebf29c56d9dc

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh && \
    apt-get purge -y curl unzip && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app /app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "soundhoard"]
