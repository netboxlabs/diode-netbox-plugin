ifneq ($(shell docker compose version 2>/dev/null),)
  DOCKER_COMPOSE := docker compose
else
  DOCKER_COMPOSE := docker-compose
endif

.PHONY: docker-compose-netbox-plugin-up
docker-compose-netbox-plugin-up:
	@$(DOCKER_COMPOSE) -f docker/docker-compose.yaml up -d --build

.PHONY: docker-compose-netbox-plugin-down
docker-compose-netbox-plugin-down:
	@$(DOCKER_COMPOSE) -f docker/docker-compose.yaml down

.PHONY: docker-compose-netbox-plugin-test
docker-compose-netbox-plugin-test:
	-@$(DOCKER_COMPOSE) -f docker/docker-compose.yaml run -u root --rm netbox ./manage.py test --keepdb netbox_diode_plugin
	@$(MAKE) docker-compose-netbox-plugin-down

.PHONY: docker-compose-netbox-plugin-test-cover
docker-compose-netbox-plugin-test-cover:
	-@$(DOCKER_COMPOSE) -f docker/docker-compose.yaml run --rm -u root -e COVERAGE_FILE=/opt/netbox/netbox/coverage/.coverage netbox sh -c "coverage run --source=netbox_diode_plugin ./manage.py test --keepdb netbox_diode_plugin && coverage xml -o /opt/netbox/netbox/coverage/report.xml && coverage report -m | tee /opt/netbox/netbox/coverage/report.txt"
	@$(MAKE) docker-compose-netbox-plugin-down
