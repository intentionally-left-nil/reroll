.DEFAULT_GOAL := help
.PHONY: help install lint format format-check typecheck test coverage-html ci clean

help:
	@echo "Targets:"
	@echo "  install           Install package + dev dependencies via uv"
	@echo "  lint              Run ruff check"
	@echo "  format            Run ruff format (rewrites files)"
	@echo "  format-check      Run ruff format --check (no rewrites, for CI)"
	@echo "  typecheck         Run ty check"
	@echo "  test              Run the corpus + unit test suite"
	@echo "  coverage-html     Run tests and open an HTML coverage report"
	@echo "  ci                Run everything CI runs: lint, format-check, typecheck, test"
	@echo "  clean             Remove caches and coverage artifacts"

install:
	uv sync --group dev

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run ty check

test:
	uv run pytest

coverage-html:
	uv run pytest --cov-report=html
	open htmlcov/index.html

ci: lint format-check typecheck test

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
