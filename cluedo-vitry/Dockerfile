# Build stage
FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim as builder

# Set environment variables
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install the project into `/app`
WORKDIR /app

# Enable bytecode compilation
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy the project files
COPY . /app

# Install the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Production stage
FROM python:3.10-slim-bookworm

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy the application from the builder stage
COPY --from=builder --chown=app:app /app /app

# Create a non-root user
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

# Change to the app directory
WORKDIR /app

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Change to the non-root user
USER app

# Expose the port
EXPOSE 8000

# Run the server
CMD ["python", "server.py"] 
