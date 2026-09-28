# OpenCloudOps
.DEFAULT_GOAL := help
PY ?= python3
PORT ?= 8081
GRAPH ?= reference-web-service
ENV ?= dev

.PHONY: help install test lint demo serve build up down logs trace iac guard clean examples

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install the package with every extra, in editable mode
	$(PY) -m pip install -e ".[all]"

test: ## Run the full test suite
	PYTHONPATH=. $(PY) -m pytest tests/ -q

lint: ## Lint
	$(PY) -m ruff check opencloudops tests scripts

examples: ## Regenerate examples/ideas/ from the sibling mcp-idea-publisher checkout
	$(PY) scripts/generate_ideas_examples.py

demo: ## End-to-end walkthrough: ground, guard, repair, emit, observe, chat
	bash scripts/demo.sh

serve: ## Run the API locally
	PYTHONPATH=. $(PY) -m uvicorn opencloudops.api:app --reload --port $(PORT)

guard: ## Run the security baseline against $(GRAPH) in $(ENV)
	PYTHONPATH=. $(PY) -m opencloudops.cli guard $(GRAPH) --env $(ENV)

iac: ## Emit Terraform for $(GRAPH) in $(ENV)
	PYTHONPATH=. $(PY) -m opencloudops.cli iac $(GRAPH) --env $(ENV) --language terraform

build: ## Build the container image
	docker build -t opencloudops:local .

up: ## Bring up the demo stack (service + collector + Jaeger on :16686)
	docker compose up -d --build
	@echo "API     http://localhost:$(PORT)/docs"
	@echo "Traces  http://localhost:16686"

down: ## Tear the demo stack down
	docker compose down -v

logs: ## Tail the audit trail
	PYTHONPATH=. $(PY) -m opencloudops.cli logs --last 40

trace: ## Print spans to the console for one request, without a collector
	OCC_OTEL_CONSOLE=1 PYTHONPATH=. $(PY) -m opencloudops.cli plan $(GRAPH) --env $(ENV)

clean: ## Remove generated output
	rm -rf out build dist .pytest_cache .ruff_cache
