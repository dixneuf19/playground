FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

COPY --from=ghcr.io/astral-sh/uv:0.13@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /uvx /bin/

LABEL org.opencontainers.image.source=https://github.com/dixneuf19/playground

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --uid 1000 --create-home app

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY server.py index.html favicon.ico ./
COPY admin ./admin
COPY css ./css
COPY images ./images
COPY posts ./posts

USER 1000
EXPOSE 8000

CMD ["python", "server.py"]
