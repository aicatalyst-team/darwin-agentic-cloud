.DEFAULT_GOAL := help
SHELL := /bin/bash

# -------------------------------------------------------------------
# Variables
# -------------------------------------------------------------------
PYTHON      := python3
UV          := uv
PACKAGE     := dac
SRC_DIRS    := dac tests
PORT        := 8787
HOST        := 127.0.0.1

# -------------------------------------------------------------------
# Help
# -------------------------------------------------------------------
.PHONY: help
help: ## Show this help
	@echo "Darwinic Agentic Cloud — developer commands"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# -------------------------------------------------------------------
# Environment setup
# -------------------------------------------------------------------
.PHONY: install
install: ## Install dependencies (prod only)
	$(UV) sync

.PHONY: dev
dev: ## Install all dev dependencies (dev + test + docs)
	$(UV) sync --extra dev --extra test --extra docs

.PHONY: clean
clean: ## Remove build artifacts, caches, and venvs
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov coverage.xml

.PHONY: clean-all
clean-all: clean ## Remove caches AND the .venv
	rm -rf .venv

# -------------------------------------------------------------------
# Quality gates
# -------------------------------------------------------------------
.PHONY: lint
lint: ## Run ruff linter
	$(UV) run ruff check $(SRC_DIRS)

.PHONY: format
format: ## Auto-format with ruff
	$(UV) run ruff format $(SRC_DIRS)
	$(UV) run ruff check --fix $(SRC_DIRS)

.PHONY: typecheck
typecheck: ## Run mypy
	$(UV) run mypy $(PACKAGE)

.PHONY: check
check: lint typecheck ## Run all static checks

# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------
.PHONY: test
test: ## Run all tests
	$(UV) run pytest

.PHONY: test-unit
test-unit: ## Run only unit tests (no Docker required)
	$(UV) run pytest -m "not integration"

.PHONY: test-integration
test-integration: ## Run integration tests (Docker required)
	$(UV) run pytest -m integration

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	$(UV) run pytest --cov=$(PACKAGE) --cov-report=term-missing --cov-report=html

# -------------------------------------------------------------------
# Run
# -------------------------------------------------------------------
.PHONY: run
run: ## Run the DAC server locally
	$(UV) run uvicorn $(PACKAGE).server.app:app --host $(HOST) --port $(PORT) --reload

.PHONY: mcp
mcp: ## Run the DAC MCP server (stdio transport)
	$(UV) run dac mcp serve

.PHONY: shell
shell: ## Open a Python shell with dac imported
	$(UV) run python -c "import dac; print(f'DAC v{dac.__version__} loaded'); import code; code.interact(local={'dac': dac})"

# -------------------------------------------------------------------
# Docker
# -------------------------------------------------------------------
.PHONY: docker-build
docker-build: ## Build the DAC server Docker image
	docker build -t dac:latest -f Dockerfile .

.PHONY: docker-build-runners
docker-build-runners: ## Build sandbox runner base images
	docker build -t dac-runner-python:latest -f Dockerfile.python-runner .
	docker build -t dac-runner-node:latest -f Dockerfile.node-runner .

.PHONY: docker-up
docker-up: ## Start the local dev stack (server + observability)
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

.PHONY: docker-down
docker-down: ## Stop the local dev stack
	docker compose down

# -------------------------------------------------------------------
# Keys (DAC signing identity)
# -------------------------------------------------------------------
.PHONY: keys-init
keys-init: ## Initialize DAC signing keys
	$(UV) run dac keys init

.PHONY: keys-show
keys-show: ## Show DAC public key and key ID
	$(UV) run dac keys show

# -------------------------------------------------------------------
# Release
# -------------------------------------------------------------------
.PHONY: build
build: clean ## Build wheel and sdist
	$(UV) build

.PHONY: version
version: ## Show the current version
	@$(UV) run python -c "import dac; print(dac.__version__)"

# -------------------------------------------------------------------
# Docs
# -------------------------------------------------------------------
.PHONY: docs
docs: ## Serve docs locally
	$(UV) run mkdocs serve

.PHONY: docs-build
docs-build: ## Build the docs site
	$(UV) run mkdocs build
