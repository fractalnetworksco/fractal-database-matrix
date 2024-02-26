.PHONY: test-ci synapse
SHELL=/bin/bash
# get makefile directory
MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
PROJECT_ENV_FILE=${MAKEFILE_DIR}fractal_database_matrix.dev.env
TEST_PROJECT_DIR=${MAKEFILE_DIR}test-config/test_project
TEST = ""

test-ci:
	docker compose up synapse --build --force-recreate -d --wait
	docker compose up test --build --force-recreate --exit-code-from test
	docker compose down

setup:
	python test-config/prepare-test.py

test:
	. ${PROJECT_ENV_FILE} && export PYTHONPATH=${TEST_PROJECT_DIR} && pytest -k ${TEST} -s --cov-config=.coveragerc --cov=fractal_database_matrix -v --asyncio-mode=auto --cov-report=lcov --cov-report=term tests/

qtest:
	. ${PROJECT_ENV_FILE} && export PYTHONPATH=${TEST_PROJECT_DIR} && pytest -k ${TEST} -s --cov-config=.coveragerc --cov=fractal_database_matrix --asyncio-mode=auto --cov-report=lcov tests/

synapse:
	docker compose -f ./synapse/docker-compose.yml up synapse -d --force-recreate --build
