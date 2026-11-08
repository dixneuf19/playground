FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

ADD . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.15-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9

# Links the ghcr.io package to the repository (lets Actions push to it).
LABEL org.opencontainers.image.source=https://github.com/dixneuf19/playground

# ffmpeg merges the video and audio streams; deno solves YouTube's JS
# challenges (the runtime yt-dlp enables by default: it runs that code with no
# file or network access). Pinned so Renovate can bump it.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin-2.9.8@sha256:b507583d3c68ba69f3ed2ce5969ae5b8b70f43421c781279c7aea06435263543 /deno /usr/local/bin/deno

# Not root. The shared /data volume must be writable by uid 1000.
RUN useradd --uid 1000 --create-home karaoke && \
    mkdir -p /data && chown karaoke:karaoke /data

COPY --from=builder /app/.venv /app/.venv
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    KARAOKE_DATA_DIR=/data \
    PORT=8000

EXPOSE 8000
VOLUME /data

USER 1000
CMD ["karaoke"]
