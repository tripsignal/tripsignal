.PHONY: help build up down restart logs shell db-shell test clean

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

test: ## Run tests (placeholder)
	@echo "Tests not yet implemented"

clean: ## Remove containers, volumes, and images
	docker compose down -v
	docker rmi tripsignal-api 2>/dev/null || true
