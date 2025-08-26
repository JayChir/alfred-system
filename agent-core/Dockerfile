# Multi-stage build for Alfred Agent Core
# Stage 1: Builder with uv for fast dependency installation
FROM python:3.11-slim AS builder

# Install uv for fast package management
# Using the official installation script for portability
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment
# uv will create .venv automatically
RUN uv sync --frozen --no-install-project

# Copy source code
COPY src/ ./src/
COPY main.py ./
# Copy README.md for pyproject.toml reference
COPY README.md ./

# Install the project itself
RUN uv sync --frozen

# Stage 2: Runtime image
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 alfred && \
    mkdir -p /app && \
    chown -R alfred:alfred /app

# Set working directory
WORKDIR /app

# Copy virtual environment and application from builder
COPY --from=builder --chown=alfred:alfred /app/.venv /app/.venv
COPY --from=builder --chown=alfred:alfred /app/src /app/src
COPY --from=builder --chown=alfred:alfred /app/main.py /app/main.py

# Switch to non-root user
USER alfred

# Set Python path to use the virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

# Expose port
EXPOSE 8080

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/healthz || exit 1

# Run the application
# Using uvicorn directly from the virtual environment
CMD ["python", "-m", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8080"]
