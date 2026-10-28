.PHONY: help install test lint build serve dev api collectstatic

help:
	@echo "install      create the venv and install backend + frontend deps"
	@echo "test         backend unit tests + frontend typecheck"
	@echo "lint         ruff check + format check"
	@echo "build        typecheck and build the React app into frontend/dist"
	@echo "dev          run the API on :8000 and Vite on :5173 together"
	@echo "collectstatic  copy frontend/dist into Django's static root"

install:
	python3.12 -m venv backend/.venv
	backend/.venv/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

test:
	cd backend && .venv/bin/python manage.py test api
	cd frontend && npm run typecheck

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .

build:
	cd frontend && npm run build

collectstatic:
	cd backend && .venv/bin/python manage.py collectstatic --noinput

dev:
	@echo "API  → http://127.0.0.1:8000"
	@echo "App  → http://localhost:5173"
	cd backend && .venv/bin/python manage.py runserver 8000 & \
	cd frontend && npm run dev; \
	kill %1 2>/dev/null || true
