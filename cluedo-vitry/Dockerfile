FROM python:3.14-slim@sha256:bc389f7dfcb21413e72a28f491985326994795e34d2b86c8ae2f417b4e7818aa

COPY --from=ghcr.io/astral-sh/uv:0.13@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /bin/

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
