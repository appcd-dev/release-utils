.PHONY: help setup generate-input generate-input-custom generate-custom-input-file fetch_changes_between_tags_from_input clean test-linear monthly-release monthly-release-no-ticket create-release-ticket create-release-ticket-only

# Configuration
PYTHON := python3
# Deployed "current" versions — default production (override: VERSION_URL=...)
VERSION_URL := https://cloud.stackgen.com/version.json
# New versions: raw .env from appcd-dist at STACKGEN_TAG (required for generate-input), e.g.
# https://raw.githubusercontent.com/appcd-dev/appcd-dist/v2026.3.12/.env
APPCD_DIST_RAW_ENV = https://raw.githubusercontent.com/appcd-dev/appcd-dist/$(STACKGEN_TAG)/.env

# Artifact root. Default for legacy targets: generated_files/
# create-release-ticket overrides this to $(FROM_REF)-$(TO_REF) (e.g. v2026.7.3-v2026.7.7)
GENERATED_DIR ?= generated_files
INPUT_FILE := $(GENERATED_DIR)/input_file/input.json
OUTPUT_FILE := $(GENERATED_DIR)/final_tag_differences.json
COMMIT_DIFF_FILE := $(GENERATED_DIR)/commit_differences_with_messages.txt
ENV_FILE := .env

# create-release-ticket defaults (override on the command line)
RELEASE_KIND ?= weekly
ASSIGNEE_QUERY ?= gaurav@stackgen.com
STATE_NAME ?= Todo

# Default target
help:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Release Utils - Tag Comparison & Ticket Extraction"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "Available targets:"
	@echo ""
	@echo "  make setup                                 - Set up environment and test Linear API"
	@echo "  make generate-input STACKGEN_TAG=<tag>    - input.json (prod version.json + raw appcd-dist .env at tag)"
	@echo "  make generate-input-custom STACKGEN_TAG=<tag> - Generate input.json (STACKGEN_TAG required)"
	@echo "  make generate-custom-input-file FROM_REF=<ref> TO_REF=<ref>"
	@echo "                                             - input.json from appcd-dist .env between two refs"
	@echo "  make fetch_changes_between_tags_from_input - Extract ticket changes between versions"
	@echo "  make monthly-release STACKGEN_TAG=<tag>    - Full pipeline: clean → prod input + .env → tickets → Linear"
	@echo "  make monthly-release-no-ticket STACKGEN_TAG=<tag> - Steps 1–3 only (no Linear issue)"
	@echo "  make create-release-ticket FROM_REF=<ref> TO_REF=<ref>"
	@echo "                                             - clean → custom input → fetch → Linear ticket"
	@echo "                                             - artifacts under <FROM_REF>-<TO_REF>/"
	@echo "  make create-release-ticket-only FROM_REF=<ref> TO_REF=<ref>"
	@echo "                                             - Create Linear ticket from <FROM_REF>-<TO_REF>/ artifacts"
	@echo "  make full-workflow                         - Run complete workflow (generate + process)"
	@echo "  make test-linear                           - Test Linear API connection"
	@echo "  make clean                                 - Remove generated_files/ (or GENERATED_DIR=…)"
	@echo ""
	@echo "Configuration:"
	@echo "  VERSION_URL   = $(VERSION_URL)"
	@echo "  GENERATED_DIR = $(GENERATED_DIR)  (default: generated_files)"
	@echo "  (generate-input) STACKGEN_TAG required — .env = appcd-dist raw at that tag"
	@echo ""
	@echo "create-release-ticket parameters:"
	@echo "  FROM_REF          required — appcd-dist base ref/tag (current)"
	@echo "  TO_REF            required — appcd-dist candidate ref/tag (new); also used as STACKGEN_TAG"
	@echo "  STACKGEN_TAG      optional — overrides title tag (default: TO_REF)"
	@echo "  RELEASE_KIND      optional — weekly|monthly (default: weekly)"
	@echo "  MONTH_LABEL       optional — e.g. \"July 2026\""
	@echo "  ASSIGNEE_QUERY    optional — default gaurav@stackgen.com"
	@echo "  STATE_NAME        optional — default Todo"
	@echo "  OUT_DIR           optional — artifact dir (default: <FROM_REF>-<TO_REF>)"
	@echo "  DRY_RUN=1         optional — preview Linear body only (skip issueCreate)"
	@echo ""
	@echo "Example:"
	@echo "  make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 MONTH_LABEL=\"July 2026\""
	@echo "  # writes to v2026.7.3-v2026.7.7/"
	@echo "  make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 DRY_RUN=1"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Setup environment and test Linear API
setup:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Setting up environment..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@if [ ! -f "$(ENV_FILE)" ]; then \
		echo "⚠️  .env file not found. Creating from template..."; \
		cp env.template $(ENV_FILE); \
		echo ""; \
		echo "📝 Please edit .env and add your LINEAR_API_KEY"; \
		echo "   Get your key from: https://linear.app/settings/api"; \
		echo ""; \
	else \
		echo "✅ .env file exists"; \
	fi
	@echo ""
	@echo "Testing Linear API connection..."
	@$(PYTHON) test_linear_api.py || echo "⚠️  Linear API not configured. Set LINEAR_API_KEY in .env"

# Generate input.json: production version.json + raw .env at appcd-dist tag STACKGEN_TAG
generate-input:
	@if [ -z "$(STACKGEN_TAG)" ]; then \
		echo "❌ STACKGEN_TAG is required (e.g. v2026.3.12)."; \
		echo "   New versions come from: https://raw.githubusercontent.com/appcd-dev/appcd-dist/<tag>/.env"; \
		exit 1; \
	fi
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Generating input.json..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "📥 Deployed versions (version.json): $(VERSION_URL)"
	@echo "📥 New versions (.env): $(APPCD_DIST_RAW_ENV)"
	@echo ""
	@$(PYTHON) generate_input_json.py \
		--version-url "$(VERSION_URL)" \
		--env-url "$(APPCD_DIST_RAW_ENV)" \
		--stackgen-tag "$(STACKGEN_TAG)" \
		--output "$(INPUT_FILE)" \
		--pretty
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✅ Generated: $(INPUT_FILE)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Custom URLs for generate-input (STACKGEN_TAG is required)
generate-input-custom:
	@if [ -z "$(STACKGEN_TAG)" ]; then \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo "❌ Error: STACKGEN_TAG is required for generate-input-custom"; \
		echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
		echo ""; \
		echo "Usage: make generate-input-custom STACKGEN_TAG=<tag>"; \
		echo "Example: make generate-input-custom STACKGEN_TAG=v2026.2.7"; \
		echo ""; \
		echo "STACKGEN_TAG is the appcd-dist tag used to fetch the .env file (e.g. v2026.2.7)."; \
		echo ""; \
		exit 1; \
	fi
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Generating input.json with custom URLs (STACKGEN_TAG=$(STACKGEN_TAG))..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "Select version.json URL:"
	@echo "  1. Stage       - https://stage.dev.stackgen.com/version.json"
	@echo "  2. Demo        - https://demo.cloud.stackgen.com/version.json"
	@echo "  3. Production  - https://cloud.stackgen.com/version.json"
	@echo "  4. Custom URL"
	@echo ""
	@read -p "Enter choice [1-4] (default: 1): " choice; \
	choice=$${choice:-1}; \
	case $$choice in \
		1) version_url="https://stage.dev.stackgen.com/version.json" ;; \
		2) version_url="https://demo.cloud.stackgen.com/version.json" ;; \
		3) version_url="https://cloud.stackgen.com/version.json" ;; \
		4) read -p "Enter custom version.json URL: " version_url ;; \
		*) echo "Invalid choice, using default"; version_url="$(VERSION_URL)" ;; \
	esac; \
	echo ""; \
	echo "Selected version.json: $$version_url"; \
	echo "📥 New versions (.env, raw at tag): $(APPCD_DIST_RAW_ENV)"; \
	echo ""; \
	$(PYTHON) generate_input_json.py \
		--version-url "$$version_url" \
		--env-url "$(APPCD_DIST_RAW_ENV)" \
		--stackgen-tag "$(STACKGEN_TAG)" \
		--output "$(INPUT_FILE)" \
		--pretty

# Generate input.json by comparing appcd-dist .env between two refs/tags/branches
# Required: FROM_REF TO_REF  (interactive prompt only if either is missing)
generate-custom-input-file:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Generating custom input.json from appcd-dist .env refs..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@from_ref="$(FROM_REF)"; \
	to_ref="$(TO_REF)"; \
	if [ -z "$$from_ref" ]; then \
		read -p "Enter FROM_REF (base tag/branch): " from_ref; \
	fi; \
	if [ -z "$$to_ref" ]; then \
		read -p "Enter TO_REF (target tag/branch): " to_ref; \
	fi; \
	if [ -z "$$from_ref" ] || [ -z "$$to_ref" ]; then \
		echo "❌ Both FROM_REF and TO_REF are required."; \
		echo "Usage: make generate-custom-input-file FROM_REF=<tag-or-branch> TO_REF=<tag-or-branch>"; \
		exit 1; \
	fi; \
	echo ""; \
	echo "Comparing appcd-dist refs: $$from_ref → $$to_ref"; \
	echo "Artifact dir: $(GENERATED_DIR)"; \
	echo "Output: $(INPUT_FILE)"; \
	echo ""; \
	$(PYTHON) generate_custom_input_file.py \
		--from-ref "$$from_ref" \
		--to-ref "$$to_ref" \
		--output "$(INPUT_FILE)" \
		--pretty

# Process all repos and extract ticket changes
# Override artifact root: make fetch_changes_between_tags_from_input GENERATED_DIR=v2026.7.3-v2026.7.7
fetch_changes_between_tags_from_input:
	@if [ ! -f "$(INPUT_FILE)" ]; then \
		echo "❌ Error: $(INPUT_FILE) not found. Run 'make generate-input' or 'make generate-custom-input-file' first."; \
		exit 1; \
	fi
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Processing all repositories and extracting ticket changes..."
	@echo "Artifact dir: $(GENERATED_DIR)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@$(PYTHON) process_all_repos.py \
		--input "$(INPUT_FILE)" \
		--output "$(OUTPUT_FILE)" \
		--commit-diff-log "$(COMMIT_DIFF_FILE)" \
		--verbose \
		--pretty
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✅ Processing complete! Output: $(OUTPUT_FILE)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Run complete workflow: generate input + process changes
full-workflow: generate-input fetch_changes_between_tags_from_input
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "✅ Full workflow completed successfully!"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Test Linear API connection
test-linear:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Testing Linear API Connection..."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@if [ -z "$$LINEAR_API_KEY" ]; then \
		echo "⚠️  LINEAR_API_KEY not set in environment."; \
		echo ""; \
		echo "To enable Linear ticket summaries:"; \
		echo "  1. Get your API key from: https://linear.app/settings/api"; \
		echo "  2. Add it to .env file: LINEAR_API_KEY=lin_api_xxx"; \
		echo "  3. Load it: source .env  OR  export \$$(cat .env | xargs)"; \
		echo ""; \
		echo "Testing without API key..."; \
		echo ""; \
		$(PYTHON) test_linear_api.py; \
	else \
		echo "✅ LINEAR_API_KEY is set"; \
		echo ""; \
		$(PYTHON) test_linear_api.py; \
	fi

# Full monthly release pipeline (see run_monthly_release.py)
# Optional: VERSION_JSON_URL=https://stage.dev.stackgen.com/version.json (default: production)
monthly-release:
	@if [ -z "$(STACKGEN_TAG)" ]; then \
		echo "Usage: make monthly-release STACKGEN_TAG=v2026.2.7"; \
		echo "Optional: VERSION_JSON_URL=<url> (default: production cloud.stackgen.com)"; \
		exit 1; \
	fi
	@EXTRA=""; \
	if [ -n "$(VERSION_JSON_URL)" ]; then EXTRA="--version-json-url $(VERSION_JSON_URL)"; fi; \
	$(PYTHON) run_monthly_release.py "$(STACKGEN_TAG)" $$EXTRA

monthly-release-no-ticket:
	@if [ -z "$(STACKGEN_TAG)" ]; then \
		echo "Usage: make monthly-release-no-ticket STACKGEN_TAG=v2026.2.7"; \
		echo "Optional: VERSION_JSON_URL=<url>"; \
		exit 1; \
	fi
	@EXTRA="--skip-ticket"; \
	if [ -n "$(VERSION_JSON_URL)" ]; then EXTRA="$$EXTRA --version-json-url $(VERSION_JSON_URL)"; fi; \
	$(PYTHON) run_monthly_release.py "$(STACKGEN_TAG)" $$EXTRA

# Full weekly/monthly release ticket pipeline:
#   clean OUT_DIR → generate-custom-input-file → fetch_changes → Linear ticket
# Artifacts written to OUT_DIR (default: <FROM_REF>-<TO_REF>/), e.g. v2026.7.3-v2026.7.7/
#
# Required: FROM_REF TO_REF
# Optional: OUT_DIR, STACKGEN_TAG, RELEASE_KIND, MONTH_LABEL, ASSIGNEE_QUERY, STATE_NAME, DRY_RUN=1
create-release-ticket:
	@if [ -z "$(FROM_REF)" ] || [ -z "$(TO_REF)" ]; then \
		echo "❌ FROM_REF and TO_REF are required."; \
		echo ""; \
		echo "Usage:"; \
		echo "  make create-release-ticket FROM_REF=<base-tag> TO_REF=<candidate-tag> [options]"; \
		echo ""; \
		echo "Options:"; \
		echo "  OUT_DIR=<dir>            artifact dir (default: <FROM_REF>-<TO_REF>)"; \
		echo "  STACKGEN_TAG=<tag>       title tag (default: TO_REF)"; \
		echo "  RELEASE_KIND=weekly|monthly   (default: weekly)"; \
		echo "  MONTH_LABEL=\"July 2026\""; \
		echo "  ASSIGNEE_QUERY=gaurav@stackgen.com"; \
		echo "  STATE_NAME=Todo"; \
		echo "  DRY_RUN=1                preview only"; \
		echo ""; \
		echo "Example:"; \
		echo "  make create-release-ticket FROM_REF=v2026.7.3 TO_REF=v2026.7.7 MONTH_LABEL=\"July 2026\""; \
		exit 1; \
	fi
	@from_safe=$$(printf '%s' "$(FROM_REF)" | sed 's|[/ :]|-|g'); \
	to_safe=$$(printf '%s' "$(TO_REF)" | sed 's|[/ :]|-|g'); \
	out_dir="$(OUT_DIR)"; \
	if [ -z "$$out_dir" ]; then out_dir="$${from_safe}-$${to_safe}"; fi; \
	echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
	echo "create-release-ticket pipeline"; \
	echo "  FROM_REF=$(FROM_REF)  →  TO_REF=$(TO_REF)"; \
	echo "  OUT_DIR=$$out_dir"; \
	echo "  STACKGEN_TAG=$(if $(STACKGEN_TAG),$(STACKGEN_TAG),$(TO_REF))"; \
	echo "  RELEASE_KIND=$(RELEASE_KIND)  DRY_RUN=$(if $(DRY_RUN),$(DRY_RUN),0)"; \
	echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
	echo ""; \
	echo "▶ Step 1/4 — clean $$out_dir"; \
	rm -rf "$$out_dir"; \
	echo "✅ Removed $$out_dir (if it existed)"; \
	echo ""; \
	echo "▶ Step 2/4 — generate-custom-input-file → $$out_dir/"; \
	$(MAKE) generate-custom-input-file \
		FROM_REF="$(FROM_REF)" \
		TO_REF="$(TO_REF)" \
		GENERATED_DIR="$$out_dir"; \
	echo ""; \
	echo "▶ Step 3/4 — fetch_changes_between_tags_from_input → $$out_dir/"; \
	$(MAKE) fetch_changes_between_tags_from_input GENERATED_DIR="$$out_dir"; \
	echo ""; \
	echo "▶ Step 4/4 — create Linear release ticket"; \
	$(MAKE) create-release-ticket-only \
		FROM_REF="$(FROM_REF)" \
		TO_REF="$(TO_REF)" \
		GENERATED_DIR="$$out_dir" \
		STACKGEN_TAG="$(if $(STACKGEN_TAG),$(STACKGEN_TAG),$(TO_REF))" \
		RELEASE_KIND="$(RELEASE_KIND)" \
		MONTH_LABEL="$(MONTH_LABEL)" \
		ASSIGNEE_QUERY="$(ASSIGNEE_QUERY)" \
		STATE_NAME="$(STATE_NAME)" \
		DRY_RUN="$(DRY_RUN)"; \
	echo ""; \
	echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; \
	echo "✅ create-release-ticket pipeline finished"; \
	echo "   Artifacts: $$out_dir/"; \
	echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Create HZ Linear release ticket from existing tag-diff artifacts only.
#
# Prefer: FROM_REF + TO_REF  → reads <FROM_REF>-<TO_REF>/final_tag_differences.json
# Or set: GENERATED_DIR=... / OUT_DIR=...
# Required: TO_REF or STACKGEN_TAG (for title)
create-release-ticket-only:
	@tag="$(STACKGEN_TAG)"; \
	if [ -z "$$tag" ]; then tag="$(TO_REF)"; fi; \
	if [ -z "$$tag" ]; then \
		echo "❌ STACKGEN_TAG or TO_REF is required."; \
		echo "Usage: make create-release-ticket-only FROM_REF=v2026.7.3 TO_REF=v2026.7.7 [DRY_RUN=1]"; \
		exit 1; \
	fi; \
	out_dir="$(GENERATED_DIR)"; \
	if [ -n "$(OUT_DIR)" ]; then out_dir="$(OUT_DIR)"; fi; \
	if [ "$$out_dir" = "generated_files" ] && [ -n "$(FROM_REF)" ] && [ -n "$(TO_REF)" ]; then \
		from_safe=$$(printf '%s' "$(FROM_REF)" | sed 's|[/ :]|-|g'); \
		to_safe=$$(printf '%s' "$(TO_REF)" | sed 's|[/ :]|-|g'); \
		out_dir="$${from_safe}-$${to_safe}"; \
	fi; \
	output_file="$$out_dir/final_tag_differences.json"; \
	services_input="$$out_dir/input_file/input.json"; \
	if [ ! -f "$$output_file" ]; then \
		echo "❌ Error: $$output_file not found."; \
		echo "   Run: make create-release-ticket FROM_REF=… TO_REF=…"; \
		exit 1; \
	fi; \
	EXTRA="--release-kind $(RELEASE_KIND) --stackgen-tag $$tag --assignee-query $(ASSIGNEE_QUERY) --state-name $(STATE_NAME)"; \
	if [ -n "$(MONTH_LABEL)" ]; then EXTRA="$$EXTRA --month-label \"$(MONTH_LABEL)\""; fi; \
	if [ "$(DRY_RUN)" = "1" ]; then EXTRA="$$EXTRA --dry-run"; fi; \
	if [ -f "$$services_input" ]; then EXTRA="$$EXTRA --services-input \"$$services_input\""; fi; \
	echo "Creating Linear ticket from $$output_file (tag=$$tag)…"; \
	eval $(PYTHON) create_monthly_release_ticket.py --input "$$output_file" $$EXTRA

# Clean generated artifacts
#   make clean                         → removes generated_files/
#   make clean GENERATED_DIR=v2026.7.3-v2026.7.7  → removes that dir only
clean:
	@echo "🧹 Cleaning $(GENERATED_DIR)..."
	@rm -rf "$(GENERATED_DIR)"
	@rm -rf __pycache__ release_pipeline/__pycache__
	@echo "✅ Cleaned $(GENERATED_DIR)/"

# Show current configuration
config:
	@echo "Current Configuration:"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "VERSION_URL    = $(VERSION_URL)"
	@echo "ENV_URL        = $(ENV_URL)"
	@echo "GENERATED_DIR  = $(GENERATED_DIR)"
	@echo "INPUT_FILE     = $(INPUT_FILE)"
	@echo "OUTPUT_FILE    = $(OUTPUT_FILE)"
	@echo "FROM_REF       = $(FROM_REF)"
	@echo "TO_REF         = $(TO_REF)"
	@echo "STACKGEN_TAG   = $(STACKGEN_TAG)"
	@echo "RELEASE_KIND   = $(RELEASE_KIND)"
	@echo "LINEAR_API_KEY = $${LINEAR_API_KEY:+Set (hidden)}$${LINEAR_API_KEY:-Not set}"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

