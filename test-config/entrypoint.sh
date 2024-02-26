#!/bin/bash

# expected environment variables:
# ENV - environment name (e.g. test, dev, prod)
# TEST_CONFIG_DIR - path to the test-config directory
# PROJECT_NAME - name of the project in snake case (e.g. fractal_database_matrix)

set -e

PREPARE_SCRIPT="$TEST_CONFIG_DIR/prepare-test.py"
# PROJECT_NAME is optional, if not set, it will be set to "fractal_database_matrix"
PROJECT_NAME="${PROJECT_NAME:-fractal_database_matrix}"
PROJECT_ENV_FILE="$TEST_CONFIG_DIR/$PROJECT_NAME.$ENV.env"

python3 "$PREPARE_SCRIPT"

# environment file should be created by prepare-test.py
source "$PROJECT_ENV_FILE"

make -C /code test PROJECT_ENV_FILE="$PROJECT_ENV_FILE" PYTHONPATH="$TEST_CONFIG_DIR/test_project" TEST_PROJECT_DIR="$TEST_CONFIG_DIR/test_project" # PYTHONPATH="$TEST_CONFIG_DIR/test_project" pytest -v -s --asyncio-mode=auto --cov=/code/fractal_database --cov-report=lcov --cov-report=term tests/
