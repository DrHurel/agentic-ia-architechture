.PHONY: help install dev test lint format clean docker-build docker-up docker-down

help:
	@echo "Available commands:"
	@echo "  make install      - Install production dependencies"
	@echo "  make dev          - Install development dependencies"
	@echo "  make test         - Run tests"
	@echo "  make lint         - Run linters"
	@echo "  make format       - Format code"
	@echo "  make clean        - Clean build artifacts"
	@echo "  make docker-build - Build Docker images"
	@echo "  make docker-up    - Start Docker Compose services"
	@echo "  make docker-down  - Stop Docker Compose services"

install:
	pip install -r requirements.txt

dev: install
	pip install pytest pytest-asyncio pytest-cov black isort mypy

test:
	pytest tests/ -v --cov=src --cov-report=term-missing

lint:
	pylint src/
	mypy src/

format:
	black src/ tests/
	isort src/ tests/

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	rm -rf .coverage htmlcov/

docker-build:
	docker-compose build

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f

# Development helpers
run-api:
	python -m src.api.main

run-prompt-analyser:
	python -m src.services.prompt_analyser.main

run-agent-code-writer:
	python -m src.agents.code_writer.main

run-agent-architect:
	python -m src.agents.architect.main

run-agent-code-quality:
	python -m src.agents.code_quality.main

run-agent-tester-whitebox:
	python -m src.agents.tester_whitebox.main

run-agent-tester-blackbox:
	python -m src.agents.tester_blackbox.main

run-agent-cicd:
	python -m src.agents.cicd.main
