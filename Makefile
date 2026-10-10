# Developer entry points. `make help` lists the targets. Settings come from .env when present.
-include .env
export

UV ?= uv
TF_DIR := infra/terraform

.DEFAULT_GOAL := help
.PHONY: help install lint format test test-unit test-e2e test-integration fixtures ingest ingest-offline \
        simulate transform dbt-athena-compile run run-fixtures reconcile dq report lambda-zip \
        tf-fmt tf-validate tf-lint tf-checkov infra-check precommit clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

install: ## Create the virtualenv (dev tools + Athena adapter)
	$(UV) sync --frozen --all-extras

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-fix lint and formatting
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test-unit: ## Fast tests (in-process moto, fixtures)
	$(UV) run pytest tests/unit

test-e2e: ## Whole pipeline on fixtures incl. dbt build and an idempotent rerun
	$(UV) run pytest -m e2e tests/e2e

test-integration: ## Endpoint path (LocalStack if FBC_AWS_ENDPOINT_URL is set, else moto server)
	$(UV) run pytest -m integration tests/integration

test: ## Every test, with coverage
	$(UV) run pytest --cov

fixtures: ## Rebuild the synthetic NSE/RBI fixtures (and the SEC ones from $$FBC_DATA_DIR/raw)
	$(UV) run python scripts/make_synthetic_fixtures.py --out tests/fixtures/raw
	@if [ -d "$(FBC_DATA_DIR)/raw/sec" ]; then $(UV) run python scripts/build_sec_fixtures.py --raw "$(FBC_DATA_DIR)/raw" --out tests/fixtures/raw; fi

ingest: ## Download SEC (+ RBI if served; NSE off), land raw Iceberg tables (raw DQ gate)
	$(UV) run fbc ingest

ingest-offline: ## Re-parse what is already in the raw directory
	$(UV) run fbc ingest --offline

simulate: ## Simulated orders + card swipes through DynamoDB -> Streams -> Lambda -> Firehose -> Iceberg
	$(UV) run fbc simulate

transform: ## dbt build (DuckDB reading the Iceberg tables)
	$(UV) run fbc transform

dbt-athena-compile: ## Compile the dbt project for Athena (no AWS account needed)
	cd dbt && AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test $(UV) run --extra athena dbt compile --target athena --profiles-dir . --no-populate-cache

run: ## ingest + simulate + transform + report on the live sources (synthetic NSE/RBI fallback)
	$(UV) run fbc run --report site

run-fixtures: ## The whole pipeline on the committed fixtures (offline), into ./data-fixtures
	rm -rf data-fixtures && mkdir -p data-fixtures && cp -r tests/fixtures/raw data-fixtures/raw
	FBC_DATA_DIR=data-fixtures FBC_TRADING_DAYS=4 FBC_CUSTOMERS=60 FBC_CARDS=150 $(UV) run fbc run --offline --report site-fixtures

reconcile: ## Print the latest ledger reconciliation (exit 4 on a break)
	$(UV) run fbc reconcile

dq: ## Re-run the raw and lake data-quality checks (exit 3 on an error)
	$(UV) run fbc dq

report: ## Write the static results site to ./site
	$(UV) run fbc report --out site

lambda-zip: ## Package src/fbc as build/lambda.zip for Terraform
	$(UV) run python scripts/build_lambda_zip.py

tf-fmt: ## terraform fmt -check
	terraform -chdir=$(TF_DIR) fmt -check -recursive

tf-validate: ## terraform init -backend=false + validate
	terraform -chdir=$(TF_DIR) init -backend=false -input=false >/dev/null
	terraform -chdir=$(TF_DIR) validate

tf-lint: ## tflint (recommended preset + AWS ruleset)
	cd $(TF_DIR) && tflint --init >/dev/null && tflint --recursive --config "$$(pwd)/.tflint.hcl"

tf-checkov: ## checkov static analysis (every skip is justified inline)
	uvx --from checkov checkov -d $(TF_DIR) --framework terraform --quiet --compact

infra-check: tf-fmt tf-validate tf-lint tf-checkov ## All Terraform checks

precommit: ## Run every pre-commit hook on every file
	$(UV) run pre-commit run --all-files

clean: ## Remove caches and build output (not data)
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov build dbt/target dbt/logs site-fixtures data-fixtures
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
