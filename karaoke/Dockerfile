FROM ghcr.io/astral-sh/uv:python3.15-trixie-slim@sha256:3ee10471b12b57f6f86d03f68c44fbd4bb8d5540efa25ab3b89159e1216b1a70 AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

ADD . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.15-slim-trixie@sha256:63bb298d2b7b88f176f51b5189f54ce6535c997682ea70885d2132f5d473a8a5

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
