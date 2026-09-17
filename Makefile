.PHONY: db-up db-down backend frontend test demo lint
db-up:
	docker compose up -d db
db-down:
	docker compose down
backend:
	cd backend && python -m uvicorn railscope.main:app --reload
frontend:
	cd frontend && npm run dev
test:
	cd backend && python -m pytest
	cd frontend && npm test -- --run
demo:
	cd backend && python -m railscope.cli demo load
lint:
	cd backend && python -m compileall railscope
	cd frontend && npm run build
