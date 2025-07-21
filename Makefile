ifneq ($(shell docker compose version 2>/dev/null),)
  DOCKER_COMPOSE := docker compose
else
  DOCKER_COMPOSE := docker-compose
endif

NETBOX_VERSION ?=
ifneq ($(NETBOX_VERSION),)
  DOCKER_PATH := docker/$(NETBOX_VERSION)
  TEST_SELECTOR := "/opt/netbox/netbox/netbox_diode_plugin/tests/$(NETBOX_VERSION)/tests/"
else
  DOCKER_PATH := docker
  TEST_SELECTOR = netbox_diode_plugin
endif 

.PHONY: docker-compose-netbox-plugin-up
docker-compose-netbox-plugin-up:
	@$(DOCKER_COMPOSE) -f $(DOCKER_PATH)/docker-compose.yaml up -d --build

.PHONY: docker-compose-netbox-plugin-down
docker-compose-netbox-plugin-down:
	@$(DOCKER_COMPOSE) -f $(DOCKER_PATH)/docker-compose.yaml down

.PHONY: docker-compose-netbox-plugin-test
docker-compose-netbox-plugin-test:
	-@$(DOCKER_COMPOSE) -f $(DOCKER_PATH)/docker-compose.yaml -f $(DOCKER_PATH)/docker-compose.test.yaml run -u root --rm netbox ./manage.py test $(TEST_FLAGS) --keepdb $(TEST_SELECTOR)
	@$(MAKE) docker-compose-netbox-plugin-down

.PHONY: docker-compose-netbox-plugin-test-lint
docker-compose-netbox-plugin-test-lint:
	-@$(DOCKER_COMPOSE) -f $(DOCKER_PATH)/docker-compose.yaml -f $(DOCKER_PATH)/docker-compose.test.yaml run -u root --rm netbox ruff check --output-format=github netbox_diode_plugin
	@$(MAKE) docker-compose-netbox-plugin-down

.PHONY: docker-compose-netbox-plugin-test-cover
docker-compose-netbox-plugin-test-cover:
	-@$(DOCKER_COMPOSE) -f $(DOCKER_PATH)/docker-compose.yaml -f $(DOCKER_PATH)/docker-compose.test.yaml run --rm -u root -e COVERAGE_FILE=/opt/netbox/netbox/coverage/.coverage netbox sh -c "coverage run --source=netbox_diode_plugin --omit=*/migrations/* ./manage.py test --keepdb $(TEST_SELECTOR) && coverage xml -o /opt/netbox/netbox/coverage/report.xml && coverage report -m | tee /opt/netbox/netbox/coverage/report.txt"
	@$(MAKE) docker-compose-netbox-plugin-down

.PHONY: docker-compose-generate-matching-docs
docker-compose-generate-matching-docs:
	@$(DOCKER_COMPOSE) -f $(DOCKER_PATH)/docker-compose.yaml -f $(DOCKER_PATH)/docker-compose.test.yaml run --rm netbox python manage.py generate_matching_docs | awk '/Generating markdown documentation.../{p=1;next} p' > ./docs/matching-criteria-documentation.md

.PHONY: docker-compose-migrate
docker-compose-migrate:
	@$(DOCKER_COMPOSE) -f $(DOCKER_PATH)/docker-compose.yaml -f $(DOCKER_PATH)/docker-compose.test.yaml run --rm netbox python manage.py migrate
