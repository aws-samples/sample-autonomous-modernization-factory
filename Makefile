.PHONY: install test clean serve docker-build bootstrap deploy destroy

# Python interpreter (override with: make PY=python)
PY ?= python3

# Install dependencies
install:
	$(PY) -m pip install -r requirements.txt

# Run tests
test:
	$(PY) -m pytest tests/ -v --tb=short

# Serve the web app locally (uvicorn). Configure via env: MF_ARTIFACTS_BUCKET,
# MF_RESULTS_BUCKET, MF_STATE_TABLE, MF_TRANSFORMER_PROJECT.
serve:
	$(PY) -m uvicorn src.api:app --host 0.0.0.0 --port 8080 --reload

# Build the API container image locally
docker-build:
	docker build --platform linux/amd64 -t modernization-webapp:local .

# Clean generated artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache

# Bootstrap the Terraform S3 backend (state bucket + lock table). Run ONCE
# before the first `make deploy`. Idempotent - safe to re-run.
bootstrap:
	cd bootstrap && terraform init && terraform apply -auto-approve

# Deploy infrastructure (requires `make bootstrap` to have run once).
# Creates application S3 buckets + DynamoDB state table + the rest of the stack.
deploy:
	terraform init && terraform plan -out=plan.out && terraform apply plan.out

# Destroy infrastructure (does NOT remove the bootstrap backend bucket/table).
destroy:
	terraform destroy -auto-approve
