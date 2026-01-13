ifneq ($(shell docker compose version 2>/dev/null),)
  DOCKER_COMPOSE := docker compose
else
  DOCKER_COMPOSE := docker-compose
endif

# Default to v4.5.x if NETBOX_VERSION is not set
NETBOX_VERSION ?= v4.5.0
# Extract minor version (e.g., v4.5.0 -> v4.5.x)
NETBOX_MINOR_VERSION := $(shell echo $(NETBOX_VERSION) | sed -E 's/^v?([0-9]+\.[0-9]+).*/v\1.x/')
DOCKER_PATH := docker/$(NETBOX_MINOR_VERSION)
DOCKER_COMMON_PATH := docker/common
TEST_SELECTOR := "/opt/netbox/netbox/netbox_diode_plugin/tests/$(NETBOX_MINOR_VERSION)/tests/"

# Export variables so they're available to docker-compose
export NETBOX_VERSION
export NETBOX_MINOR_VERSION

.PHONY: docker-compose-netbox-plugin-up
docker-compose-netbox-plugin-up:
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMMON_PATH)/docker-compose.yaml up -d --build

.PHONY: docker-compose-netbox-plugin-down
docker-compose-netbox-plugin-down:
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMMON_PATH)/docker-compose.yaml down

.PHONY: docker-compose-netbox-plugin-test
docker-compose-netbox-plugin-test:
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMMON_PATH)/docker-compose.yaml -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run -u root --rm netbox ./manage.py test $(TEST_FLAGS) --keepdb $(TEST_SELECTOR); \
	EXIT_CODE=$$?; \
	$(MAKE) docker-compose-netbox-plugin-down; \
	exit $$EXIT_CODE

.PHONY: docker-compose-netbox-plugin-test-lint
docker-compose-netbox-plugin-test-lint:
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMMON_PATH)/docker-compose.yaml -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run -u root --rm netbox ruff check --output-format=github netbox_diode_plugin; \
	EXIT_CODE=$$?; \
	$(MAKE) docker-compose-netbox-plugin-down; \
	exit $$EXIT_CODE

.PHONY: docker-compose-netbox-plugin-test-cover
docker-compose-netbox-plugin-test-cover:
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMMON_PATH)/docker-compose.yaml -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run --rm -u root -e COVERAGE_FILE=/opt/netbox/netbox/coverage/.coverage netbox sh -c "coverage run --source=netbox_diode_plugin --omit=*/migrations/* ./manage.py test --keepdb $(TEST_SELECTOR) && coverage xml -o /opt/netbox/netbox/coverage/report.xml && coverage report -m | tee /opt/netbox/netbox/coverage/report.txt"; \
	EXIT_CODE=$$?; \
	$(MAKE) docker-compose-netbox-plugin-down; \
	exit $$EXIT_CODE

.PHONY: docker-compose-generate-matching-docs
docker-compose-generate-matching-docs:
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMMON_PATH)/docker-compose.yaml -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run --rm netbox python manage.py generate_matching_docs | awk '/Generating markdown documentation.../{p=1;next} p' > ./docs/matching-criteria-documentation.md

.PHONY: docker-compose-migrate
docker-compose-migrate:
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMMON_PATH)/docker-compose.yaml -f $(DOCKER_COMMON_PATH)/docker-compose.test.yaml run --rm netbox python manage.py migrate
