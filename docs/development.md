# Development

Use `docker compose up -d db` to start PostGIS. Run `cd backend; alembic upgrade
head` against an empty database after installing dependencies. The included
demo API uses the deterministic repository so core unit tests do not require a
running external service.

Run `python -m pytest` in `backend`, then `npm test -- --run` and `npm run
build` in `frontend`. The desktop shell is a separate Tauri 2 process and does
not embed database or domain logic.
