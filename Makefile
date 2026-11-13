PYTHON ?= python3

.PHONY: install run test test-cov demo

install:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

run:
	.venv/bin/uvicorn app.main:app --reload --port 8000

test:
	.venv/bin/python -m pytest

test-cov:
	.venv/bin/python -m pytest --cov=app --cov-report=term-missing

demo:
	./examples/demo.sh
