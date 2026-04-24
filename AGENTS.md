# Repository Guidelines

## Project Structure & Module Organization
This repository is a compact Flask service centered on a single application file:

- `app.py`: Flask routes, DB access, auth helpers, versioning, and error handling.
- `.venv/`: local virtual environment for development only; do not commit changes here.
- `__pycache__/`: generated Python bytecode; ignore it.
- `RESUME`: local workspace artifact, not part of the app.

There is no `tests/` package yet. When tests are added, place them under `tests/` and keep application code out of ad hoc scripts.

## Build, Test, and Development Commands
Use the local virtual environment in this repository.

- `source .venv/bin/activate`: activate the project environment.
- `.venv/bin/python app.py`: run the Flask app locally.
- `.venv/bin/python -m py_compile app.py`: verify syntax before committing.
- `.venv/bin/python - <<'PY' ... PY`: run focused route checks with Flask’s test client.

Example:
```bash
.venv/bin/python -m py_compile app.py
```

## Coding Style & Naming Conventions
Follow standard Python conventions:

- 4-space indentation, no tabs.
- `snake_case` for functions, variables, and helper names.
- Keep route handlers explicit and descriptive, for example `select_from_table`.
- Prefer shared helpers for repeated validation, response formatting, and error handling.

No formatter or linter is configured yet, so keep edits small, readable, and internally consistent.

## Testing Guidelines
There is no formal automated test suite yet. Minimum validation for changes:

- run `py_compile`
- exercise changed routes with Flask’s test client
- check both success and failure responses

When adding tests, use names like `test_invalid_table_returns_400` and keep them under `tests/`.

## Commit & Pull Request Guidelines
Current Git history uses short, imperative commit messages, for example:

- `Initialized repo`
- `Initial app version 0.1.0`

Follow the same style. Keep each commit focused on one behavior change. For pull requests, include:

- a brief summary of the API or behavior change
- manual test evidence or sample request/response output
- any config, dependency, or migration notes

## Security & Configuration Tips
Do not add secrets or production credentials in new changes. Prefer environment variables for DB URLs, JWT settings, and debug configuration.
