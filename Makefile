# Developer commands for the Foundry eval sandbox. `make` lists them.
#
# New workstation: install asdf 0.16+ (https://asdf-vm.com), then `make bootstrap`. Tool versions are pinned in
# .tool-versions (uv, Node, AWS CLI) and .python-version (Python, installed by uv rather than built by asdf).

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
export PATH := $(or $(ASDF_DATA_DIR),$(HOME)/.asdf)/shims:$(PATH)

RUFF := uvx ruff@0.14.0
ASDF_PLUGINS := uv nodejs awscli

.PHONY: help bootstrap doctor sync test test-all lint typecheck web-test web-build types check ui deploy status logs deployments

help: ## List the targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  \033[1m%-11s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- setting up -----------------------------------------------------------------------------------------------------

bootstrap: ## Set up this workstation: pinned tools, Python, all dependencies; then `make doctor`
	@command -v asdf >/dev/null || { echo "asdf isn't installed: see https://asdf-vm.com (0.16+), then run make bootstrap again"; exit 1; }
	@for p in $(ASDF_PLUGINS); do asdf plugin list 2>/dev/null | grep -qx "$$p" || asdf plugin add "$$p"; done
	@for p in $(ASDF_PLUGINS); do asdf install "$$p"; done  # this repo's tools only (not a global .tool-versions)
	uv python install
	uv sync
	cd web && npm ci
	cd infra && npm ci
	@$(MAKE) --no-print-directory doctor

doctor: ## Check the tools and versions this repo needs (AWS and podman are optional: deploys only)
	@ok=1; \
	want() { grep "^$$1 " .tool-versions | awk '{print $$2}'; }; \
	check() { if [ "$$2" = "$$3" ]; then printf "  ok    %-8s %s\n" "$$1" "$$2"; else printf "  FAIL  %-8s %s (want %s)\n" "$$1" "$${2:-missing}" "$$3"; ok=0; fi; }; \
	check uv "$$(uv --version 2>/dev/null | awk '{print $$2}')" "$$(want uv)"; \
	check node "$$(node --version 2>/dev/null | tr -d v)" "$$(want nodejs)"; \
	check aws "$$(aws --version 2>/dev/null | awk '{print $$1}' | cut -d/ -f2)" "$$(want awscli)"; \
	check python "$$(uv run --quiet python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" "$$(cat .python-version)"; \
	if command -v uvx >/dev/null; then echo "  ok    uvx      on PATH (foundry run starts the RODA MCP server with it)"; else echo "  FAIL  uvx      not on PATH"; ok=0; fi; \
	if [ -d .venv ] && uv sync --locked --check >/dev/null 2>&1; then echo "  ok    deps     Python (uv.lock)"; else echo "  FAIL  deps     Python: run make sync"; ok=0; fi; \
	for d in web infra; do if [ -d $$d/node_modules ]; then echo "  ok    deps     $$d/ (npm)"; else echo "  FAIL  deps     $$d/: run cd $$d && npm ci"; ok=0; fi; done; \
	if aws sts get-caller-identity >/dev/null 2>&1; then echo "  ok    aws      credentials for $$(aws sts get-caller-identity --query Account --output text) (profile $${AWS_PROFILE:-default})"; \
	else echo "  --    aws      no credentials (only needed for Bedrock configs and deploys: aws sso login)"; fi; \
	if command -v podman >/dev/null || command -v docker >/dev/null; then echo "  ok    images   $$(command -v podman >/dev/null && echo podman || echo docker) (deploys build the Lambda image)"; \
	else echo "  --    images   no podman or docker (only needed to deploy)"; fi; \
	[ $$ok = 1 ] && echo "Ready." || { echo "Not ready: see FAIL lines (make bootstrap fixes most)."; exit 1; }

sync: ## Install the Python dependencies from uv.lock
	uv sync

# --- checking -------------------------------------------------------------------------------------------------------

test: ## Python tests, without the ones that need internet (the real RODA server)
	uv run pytest -q -m "not network"

test-all: ## Every Python test, as CI runs them
	uv run pytest -q

lint: ## Ruff over src, tests, scripts and infra (CI runs this too)
	$(RUFF) check src tests scripts infra

typecheck: ## TypeScript types of the web app
	cd web && npx tsc --noEmit

web-test: ## The web app's unit tests (Vitest)
	cd web && npm test

web-build: ## Build the web app into src/foundry/ui/web_dist (commit the result)
	cd web && npm run build

types: ## Regenerate web/src/types.ts from the API's models (commit the result)
	uv run python scripts/gen_api_types.py

check: lint typecheck test web-test ## Everything worth running before a commit

# --- running --------------------------------------------------------------------------------------------------------

ui: ## The local UI and API on http://127.0.0.1:8765
	uv run foundry ui

CLOUD := scripts/cloud.sh $(if $(NAME),--name $(NAME))

deploy: ## Deploy to AWS: the main deployment, or yours with NAME=<you> (options: ARGS="--email you@example.com")
	$(CLOUD) deploy $(ARGS)

status: ## A deployment's URL, build and spend cap (NAME=<you> for yours)
	$(CLOUD) status

logs: ## CloudWatch logs: make logs [NAME=<you>] SERVICE=api|worker|probe|batch [ARGS="--since 2h"]
	$(CLOUD) logs $(or $(SERVICE),api) $(ARGS)

deployments: ## Every deployment in the AWS account
	scripts/cloud.sh list
