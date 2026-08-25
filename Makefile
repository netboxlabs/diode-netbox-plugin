ifneq ($(shell docker compose version 2>/dev/null),)
  DOCKER_COMPOSE := docker compose
else
  DOCKER_COMPOSE := docker-compose
endif

# Default to v4.5.x if NETBOX_VERSION is not set
NETBOX_VERSION ?= v4.5.5
# Extract minor version (e.g., v4.5.0 -> v4.5.x)
NETBOX_MINOR_VERSION := $(shell echo $(NETBOX_VERSION) | sed -E 's/^v?([0-9]+\.[0-9]+).*/v\1.x/')
DOCKER_PATH := docker/$(NETBOX_MINOR_VERSION)
DOCKER_COMMON_PATH := docker/common
DOCKER_OVERRIDE := $(DOCKER_PATH)/docker-compose.override.yaml
COMPOSE_FILES := -f $(DOCKER_COMMON_PATH)/docker-compose.yaml $(if $(wildcard $(DOCKER_OVERRIDE)),-f $(DOCKER_OVERRIDE))
TEST_SELECTOR := "/opt/netbox/netbox/netbox_diode_plugin/tests/$(NETBOX_MINOR_VERSION)/tests/"

# Export variables so they're available to docker-compose
export NETBOX_VERSION
export NETBOX_MINOR_VERSION

.PHONY: docker-compose-netbox-plugin-up
docker-compose-netbox-plugin-up:
	@$(DOCKER_COMPOSE) $(COMPOSE_FILES) up -d --build

.PHONY: docker-compose-netbox-plugin-down
docker-compose-netbox-plugin-down:
	@$(DOCKER_COMPOSE) $(COMPOSE_FILES) down

.PHONY: docker-compose-netbox-plugin-test
docker-compose-netbox-plugin-test:
	@$(DOCKER_COMPOSE) $(COMPOSE_FILES) -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run -u root --rm netbox ./manage.py test $(TEST_FLAGS) --keepdb $(TEST_SELECTOR); \
	EXIT_CODE=$$?; \
	$(MAKE) docker-compose-netbox-plugin-down; \
	exit $$EXIT_CODE

.PHONY: docker-compose-netbox-plugin-test-lint
docker-compose-netbox-plugin-test-lint:
	@$(DOCKER_COMPOSE) $(COMPOSE_FILES) -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run -u root --rm netbox ruff check --output-format=github netbox_diode_plugin; \
	EXIT_CODE=$$?; \
	$(MAKE) docker-compose-netbox-plugin-down; \
	exit $$EXIT_CODE

.PHONY: docker-compose-netbox-plugin-test-cover
docker-compose-netbox-plugin-test-cover:
	@$(DOCKER_COMPOSE) $(COMPOSE_FILES) -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run --rm -u root -e COVERAGE_FILE=/opt/netbox/netbox/coverage/.coverage netbox sh -c "coverage run --source=netbox_diode_plugin --omit=*/migrations/* ./manage.py test --keepdb $(TEST_SELECTOR) && coverage xml -o /opt/netbox/netbox/coverage/report.xml && coverage report -m | tee /opt/netbox/netbox/coverage/report.txt"; \
	EXIT_CODE=$$?; \
	$(MAKE) docker-compose-netbox-plugin-down; \
	exit $$EXIT_CODE

.PHONY: docker-compose-generate-matching-docs
# Writes via temp files and only replaces the doc on success. The original form
# redirected straight onto the doc, so a failing generator truncated it to zero
# while make still reported success -- awk exits 0 on empty input, and the
# status of a pipeline is the status of its LAST command, so the generator's
# failure was never seen. An unmigrated database is enough to trigger it (run
# docker-compose-migrate first).
#
# The generator therefore runs UNPIPED into $$raw, so `&&` sees its real exit
# status, and the filter reads that file afterwards. `set -o pipefail` would
# also have caught it, but make runs recipes with /bin/sh and this Makefile
# sets no SHELL: on a dash older than 0.5.12 that line is "Illegal option -o
# pipefail" and the recipe dies before running anything. Setting SHELL := bash
# would change every other recipe in this file, so the pipe goes, not the
# shell (dash 0.5.11 rejects it, 0.5.12 accepts it -- bullseye vs trixie). The
# emptiness check alone is not a substitute -- it cannot see a generator that
# failed AFTER emitting the marker line.
#
# chmod before the mv because mktemp creates 0600 and mv carries that mode onto
# the doc, where the old redirect-onto-the-file form left the existing 644
# alone. Git only records the exec bit, so this never reached a commit -- it
# just made the working copy unreadable to anyone but its owner.
docker-compose-generate-matching-docs:
	@raw=$$(mktemp) && doc=$$(mktemp) && trap 'rm -f "$$raw" "$$doc"' EXIT && \
	$(DOCKER_COMPOSE) $(COMPOSE_FILES) -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run --rm netbox python manage.py generate_matching_docs > "$$raw" && \
	awk '/Generating markdown documentation.../{p=1;next} p' "$$raw" > "$$doc" && \
	if [ ! -s "$$doc" ]; then echo "generate_matching_docs produced no output; doc left unchanged" >&2; exit 1; fi && \
	chmod 644 "$$doc" && mv "$$doc" ./docs/matching-criteria-documentation.md

.PHONY: docker-compose-migrate
docker-compose-migrate:
	@$(DOCKER_COMPOSE) $(COMPOSE_FILES) -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run --rm netbox python manage.py migrate
