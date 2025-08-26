# Makefile for agent-core development
# Uses uv for package management and virtual environment

.PHONY: help install run build lint fmt test db-migrate clean dev-setup

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
	@echo "Utilities:"
	@echo "  clean       Clean build artifacts"

# Development setup
install:
	PATH="$$HOME/.local/bin:$$PATH" uv sync --native-tls

dev-setup: install
	PATH="$$HOME/.local/bin:$$PATH" uv run pre-commit install

# Run the application
run:
	PATH="$$HOME/.local/bin:$$PATH" uv run uvicorn src.app:app --host 0.0.0.0 --port 8080 --reload

# Build the application (for Docker)
build:
	PATH="$$HOME/.local/bin:$$PATH" uv build

# Code quality
lint:
	PATH="$$HOME/.local/bin:$$PATH" uv run ruff check src/ tests/
	PATH="$$HOME/.local/bin:$$PATH" uv run black --check src/ tests/
	PATH="$$HOME/.local/bin:$$PATH" uv run isort --check-only src/ tests/

fmt:
	PATH="$$HOME/.local/bin:$$PATH" uv run ruff format src/ tests/
	PATH="$$HOME/.local/bin:$$PATH" uv run black src/ tests/
	PATH="$$HOME/.local/bin:$$PATH" uv run isort src/ tests/

# Testing
test:
	PATH="$$HOME/.local/bin:$$PATH" uv run pytest tests/ -v

# Database operations (will implement with alembic later)
db-migrate:
	@echo "Database migrations will be implemented in Week 2/3"
	@echo "TODO: PATH=\"$$HOME/.local/bin:$$PATH\" uv run alembic upgrade head"

# Cleanup
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name "__pycache__" -delete
	find . -type f -name "*.pyc" -delete
