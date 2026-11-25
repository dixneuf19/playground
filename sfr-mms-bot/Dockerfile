FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

LABEL org.opencontainers.image.source=https://github.com/dixneuf19/playground

COPY --from=ghcr.io/astral-sh/uv:0.13@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY ./sfr_mms_bot ./sfr_mms_bot

CMD ["python", "-m", "sfr_mms_bot.bot"]
