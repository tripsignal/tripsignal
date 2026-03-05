.PHONY: help build up down restart logs shell db-shell test lint typecheck fmt security-scan dep-audit ci clean

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build Docker images
	docker compose build

up: ## Start all services
	docker compose up -d

up-build: ## Build and start all services
	docker compose up --build -d

down: ## Stop all services
	docker compose down

restart: ## Restart all services
	docker compose restart

logs: ## Show logs from all services
	docker compose logs -f

logs-api: ## Show logs from API service
	docker compose logs -f api

logs-db: ## Show logs from PostgreSQL service
	docker compose logs -f postgres

shell: ## Open shell in API container
	docker compose exec api /bin/bash

db-shell: ## Open PostgreSQL shell
	docker compose exec postgres psql -U postgres -d tripsignal

test: ## Run pytest suite
	python -m pytest backend/tests/ -v --tb=short

lint: ## Run ruff linter
	ruff check backend/app/

typecheck: ## Run mypy type checker
	mypy backend/app/ --ignore-missing-imports

fmt: ## Auto-format and fix lint issues
	ruff check --fix backend/app/
	ruff format backend/app/

security-scan: ## Run bandit security scanner
	bandit -r backend/app/ -ll -ii

dep-audit: ## Audit dependencies for known vulnerabilities
	pip-audit -r requirements.txt

ci: ## Run all checks (lint + typecheck + test + security-scan)
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) security-scan

clean: ## Remove containers, volumes, and images
	docker compose down -v
	docker rmi tripsignal-api 2>/dev/null || true
