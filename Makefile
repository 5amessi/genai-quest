.PHONY: install run test eval check docker-build docker-up docker-down smoke validate-json

PYTHON ?= python

install:
	$(PYTHON) -m pip install --editable ".[dev]"

run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	$(PYTHON) -m pytest -q

eval:
	$(PYTHON) scripts/evaluate.py --dataset evaluation/dataset.jsonl --output evaluation/results.json

check: validate-json
	$(PYTHON) -m compileall -q app tests scripts
	ruff check app tests scripts
	$(PYTHON) -m pytest -q

docker-build:
	docker build --tag kentrick-knowledge-platform:local .

docker-up:
	docker compose up --build --detach

docker-down:
	docker compose down

smoke:
	curl --fail --silent http://localhost:8000/health/live
	curl --fail --silent http://localhost:8000/health/ready

validate-json:
	$(PYTHON) -m json.tool postman/Kentrick-Knowledge-Platform.postman_collection.json > /dev/null
	$(PYTHON) -m json.tool postman/local.postman_environment.json > /dev/null
	$(PYTHON) -m json.tool infra/main.parameters.example.json > /dev/null
