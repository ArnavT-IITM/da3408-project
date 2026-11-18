.PHONY: setup up down logs test pipeline replay config

setup:
	mkdir -p data artifacts state
	sh infra/create-certs.sh

up: setup
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs --tail=80

test: setup
	docker compose --profile tools run --rm --no-deps checks

pipeline:
	curl --fail --show-error -X POST http://localhost:$${AIRCAST_API_PORT:-18000}/pipeline/run

replay:
	curl --fail --show-error -X POST -H 'Content-Type: application/json' \
	  -d '{"days":30,"delay":2}' http://localhost:$${AIRCAST_API_PORT:-18000}/replay

config:
	docker compose config --quiet
