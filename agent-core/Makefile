# Makefile for agent-core development
# Uses uv for package management and virtual environment

.PHONY: help install run build lint fmt test db-migrate clean dev-setup docker-build docker-run

# Detect if we're in a container or CI environment
IN_CONTAINER := $(shell [ -f /.dockerenv ] && echo 1 || echo 0)
CI := $(shell [ -n "$$CI" ] && echo 1 || echo 0)

# Set UV_PATH based on environment
# In containers, uv will be in PATH already
# On local dev, check common locations
ifeq ($(IN_CONTAINER),1)
    UV := uv
else ifeq ($(CI),1)
    UV := uv
else
    # Try to find uv in common locations
    UV := $(shell command -v uv 2>/dev/null || echo "$$HOME/.local/bin/uv")
endif

# Default target
help:
	@echo "Alfred Agent Core - Available commands:"
	@echo ""
	@echo "Development:"
	@echo "  install     Install dependencies"
	@echo "  dev-setup   Full development setup (install + pre-commit)"
	@echo "  run         Start development server"
	@echo "  build       Build the application"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint        Run linting (ruff check)"
	@echo "  fmt         Format code (ruff format + black + isort)"
	@echo "  test        Run test suite"
	@echo ""
	@echo "Database:"
	@echo "  db-migrate  Run database migrations"
	@echo ""
	@echo "Docker:"
	@echo "  docker-build  Build Docker image"
	@echo "  docker-run    Run in Docker container"
	@echo ""
	@echo "Utilities:"
	@echo "  clean       Clean build artifacts"

# Development setup
install:
	$(UV) sync --native-tls

dev-setup: install
	$(UV) run pre-commit install

# Run the application
run:
	$(UV) run uvicorn src.app:app --host 0.0.0.0 --port 8080 --reload

# Build the application (for Docker)
build:
	$(UV) build

# Code quality
lint:
	$(UV) run ruff check src/ tests/
	$(UV) run black --check src/ tests/
	$(UV) run isort --check-only src/ tests/

fmt:
	$(UV) run ruff format src/ tests/
	$(UV) run black src/ tests/
	$(UV) run isort src/ tests/

# Testing
test:
	$(UV) run pytest tests/ -v

# Database operations (will implement with alembic later)
db-migrate:
	@echo "Database migrations will be implemented in Week 2/3"
	@echo "TODO: $(UV) run alembic upgrade head"

# Docker operations
docker-build:
	docker build -t alfred-agent-core:latest .

docker-run:
	docker run -p 8080:8080 --env-file .env alfred-agent-core:latest

# Cleanup
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name "__pycache__" -delete
	find . -type f -name "*.pyc" -delete
