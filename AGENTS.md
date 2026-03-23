# Repository Guidelines

## Project Structure & Module Organization
This repository is currently a single-file Flask service:

- [`app.py`](/home/user/dev/flask-app/app.py): main application, route handlers, database access, auth helpers, and error handling.
- `.venv/`: local virtual environment for development only. Do not commit it.
- `__pycache__/`: Python bytecode cache. Treat as generated output.

If the project grows, keep route handlers, database helpers, and auth logic in separate modules under an `app/` package instead of expanding `app.py` further.

## Build, Test, and Development Commands
Use the local virtual environment when working in this repository.

- `source .venv/bin/activate`: activate the local Python environment.
- `.venv/bin/python -m py_compile app.py`: syntax check the application.
- `.venv/bin/python app.py`: run the Flask app locally.
- `.venv/bin/python - <<'PY' ... PY`: use small inline scripts for route checks with Flask’s test client.

If dependencies are missing, install them into `.venv`, not system Python.

## Coding Style & Naming Conventions
Follow standard Python style:

- 4-space indentation, no tabs.
- `snake_case` for functions, variables, and route helpers.
- Keep route names explicit, for example `select_from_table` or `bulk_delete_from_csv`.
- Prefer shared helpers for repeated behaviors such as validation and JSON error responses.

There is no formatter configured in this repository yet. Keep edits minimal, readable, and consistent with existing Flask patterns.

## Testing Guidelines
There is no formal test suite yet. For now:

- Run `.venv/bin/python -m py_compile app.py` before submitting changes.
- Use Flask’s `app.test_client()` for route-level checks.
- Cover both success and failure paths, especially validation errors and database failures.

When adding tests later, place them under `tests/` and use names like `test_invalid_table_returns_400`.

## Commit & Pull Request Guidelines
Git history is not available in this workspace, so no repository-specific commit convention could be derived. Use short, imperative commit messages such as `Unify API error responses`.

For pull requests:

- describe the behavior change clearly
- include sample request/response output for API changes
- mention any environment or dependency changes
- note manual test coverage and unresolved risks

## Security & Configuration Tips
Do not hardcode secrets or production database credentials in new changes. Prefer environment variables for JWT secrets, database URLs, and debug settings.
