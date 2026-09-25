# FreeRADIUS Provisioning API

This repository contains a single-file Flask application, [`app.py`](./app.py), used to provision and manage MariaDB tables commonly used by FreeRADIUS. It exposes JSON and CSV endpoints for CRUD-style operations, bulk import/export, authentication, and version reporting.

## What the Script Does

`app.py` combines the following responsibilities:

- Flask app setup and JWT configuration
- SQLAlchemy engine/session management for MariaDB
- SQLite-backed local user registry for `/register` and `/login`
- unified API error handling through `ApiError`
- retry handling for Galera deadlocks and transient DB conflicts via `galera_retry`
- CSV import/export helpers for bulk provisioning workflows

## Key Internal Functions

- `get_sqlite_connection()`: opens the local SQLite credentials database.
- `init_sqlite_db()`: creates the `users` table if needed.
- `success_response(payload, status_code=200)`: standard JSON success envelope.
- `validate_table_name(table_name)`: allows only whitelisted tables.
- `get_json_body(required=False)`: safely reads JSON request bodies.
- `get_uploaded_csv()`: validates uploaded CSV files.
- `galera_retry(max_retries=5)`: retries deadlocks, lock waits, and similar Galera errors.
- `create_table_class(table_name)`: reflects an existing MariaDB table into a SQLAlchemy model.
- `execute_delete_query(table_name, params)`: deletes rows matching JSON criteria.
- `shutdown_session()`: removes scoped DB sessions after each request.

Allowed MariaDB tables:

- `radcheck`
- `radreply`
- `radusergroup`
- `radgroupcheck`
- `radgroupreply`
- `prohibited`
- `whiteboned`

## Available Routes

### Basic / Auth

- `POST /register`
  JSON body: `{"username":"user","password":"pass"}`
- `POST /login`
  JSON body: `{"username":"user","password":"pass"}`
- `GET /token/check`
- `GET /`
- `GET /hello`
- `GET /version`
- `GET /chkcluster`

### Galera Health

`GET /chkcluster` reads Galera global variables and status through the configured
`DB_URL`. It returns HTTP 200 when replication is enabled and the connected node
is Primary, connected, ready, and Synced (state 4), with a positive cluster size.
HTTP 503 indicates an unhealthy node, disabled/unavailable Galera, or a database
query failure. No database changes are made.

```bash
curl http://127.0.0.1:5000/chkcluster
```

A healthy response includes `success: true`, `healthy: true`, `galera_enabled`,
`cluster_size`, `scope: "connected_node"`, selected raw `wsrep_*` values in
`status`, and an empty `reasons` list. Unhealthy responses use the standard error
envelope with health information and reasons under `details`. Database failures
return a generic error without connection credentials.

This checks the connected node's view, including its reported cluster size; it
does not contact every member or enforce an expected number of nodes. A smaller
Primary component can still pass. Behind a proxy, the check reflects whichever
backend serves the connection. Like the table routes, authentication is disabled
by default; enable its `@jwt_required()` decorator when needed.

The status checks follow the
[MariaDB Galera monitoring guidance](https://mariadb.com/docs/galera-cluster/high-availability/monitoring-mariadb-galera-cluster).

### Table Operations

- `GET /select/<table_name>`
  Query params: `columns`, `where`, `order_by`, `limit`
- `POST /insert/<table_name>`
  JSON body with column/value pairs
- `POST /delete/<table_name>`
  JSON body with exact-match column filters
- `POST /truncate/<table_name>`

### Bulk CSV Operations

- `POST /populate_old/<table_name>`
  Multipart upload with `file`; optional `has_headers`; query param `batch_size`
- `POST /populate/<table_name>`
  Multipart upload with `file`; optional `has_headers`; uses `LOAD DATA LOCAL INFILE`
- `POST /bulk_delete/<table_name>`
  Multipart upload with `file`; optional `has_headers`
- `GET /export/<table_name>`
  Query params: `where`, `order_by`, `limit`

## curl Examples

Assume the app is running on `http://127.0.0.1:5000`.

Register a user:

```bash
curl -X POST http://127.0.0.1:5000/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"secret"}'
```

Example response:

```json
{"success": true, "message": "User created successfully"}
```

Login and receive a JWT token:

```bash
curl -X POST http://127.0.0.1:5000/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"secret"}'
```

Example response:

```json
{"success": true, "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOi..."}
```

Read rows from `radcheck`:

```bash
curl "http://127.0.0.1:5000/select/radcheck?limit=10&order_by=id"
```

Example response:

```json
{"success": true, "data": [{"id": 1, "username": "demo", "attribute": "Cleartext-Password"}]}
```

Insert a row:

```bash
curl -X POST http://127.0.0.1:5000/insert/radreply \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","attribute":"Reply-Message","op":":=","value":"ok"}'
```

Example response:

```json
{"success": true, "message": "Insert successful", "rows_affected": 1}
```

Delete matching rows:

```bash
curl -X POST http://127.0.0.1:5000/delete/radreply \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","attribute":"Reply-Message"}'
```

Example response:

```json
{"success": true, "message": "Delete operation successful. Affected rows: 1"}
```

Upload CSV in batch mode:

```bash
curl -X POST "http://127.0.0.1:5000/populate_old/radcheck?batch_size=250" \
  -F "file=@radcheck.csv" \
  -F "has_headers=true"
```

Example response:

```json
{"success": true, "message": "Data imported successfully", "rows_inserted": 250, "batches": 1}
```

Upload CSV with `LOAD DATA LOCAL INFILE`:

```bash
curl -X POST http://127.0.0.1:5000/populate/radcheck \
  -F "file=@radcheck.csv" \
  -F "has_headers=true"
```

Example response:

```json
{"success": true, "message": "Data imported successfully using LOAD DATA", "rows_inserted": 250}
```

Export a table:

```bash
curl "http://127.0.0.1:5000/export/radcheck?limit=100" -o radcheck_export.csv
```

Example output file:

```csv
username,attribute,op,value
demo,Cleartext-Password,:=,secret
```

Get the app version:

```bash
curl http://127.0.0.1:5000/version
```

Example response:

```json
{"success": true, "version": "0.1.0"}
```

## JWT Authentication

JWT authentication support exists in the code, but it is off by default. Route protection is currently disabled because the `@jwt_required()` decorators are commented out in [`app.py`](./app.py).

To enable JWT protection:

1. Open [`app.py`](./app.py).
2. Find the commented lines `#@jwt_required()`.
3. Replace them with `@jwt_required()` on the routes you want to protect, such as:
   - `/token/check`
   - `/`
   - `/select/<table_name>`
   - `/delete/<table_name>`
   - `/insert/<table_name>`
   - `/populate_old/<table_name>`
   - `/populate/<table_name>`
   - `/truncate/<table_name>`
   - `/bulk_delete/<table_name>`
   - `/export/<table_name>`
4. Set a strong `JWT_SECRET_KEY` environment variable before starting the app.

Example:

```bash
export JWT_SECRET_KEY='replace-this-with-a-long-random-secret'
.venv/bin/python app.py
```

How to use JWT after enabling it:

1. Register a user with `/register`.
2. Login with `/login` and copy `access_token` from the response.
3. Send the token in the `Authorization` header:

```bash
TOKEN="paste-access-token-here"

curl http://127.0.0.1:5000/select/radcheck?limit=5 \
  -H "Authorization: Bearer $TOKEN"
```

Example response:

```json
{"success": true, "data": [{"id": 1, "username": "demo"}]}
```

You can also check token status with:

```bash
curl http://127.0.0.1:5000/token/check \
  -H "Authorization: Bearer $TOKEN"
```

Example response:

```json
{"success": true, "expires_at": "2026-04-24T12:00:00+00:00", "seconds_remaining": 3599.8, "is_expired": false}
```

Note: if you protect `/`, `get_jwt_identity()` will return the username stored in the token. If JWT is not enabled on `/`, that field may be empty.

## Running Locally

```bash
source .venv/bin/activate
.venv/bin/python -m py_compile app.py
.venv/bin/python app.py
```

## Running with Docker

Build and start a containerized version (Nginx on host port 8000, Gunicorn on loopback 5000):

```bash
docker compose build --no-cache && docker compose up -d
```

The compose file uses host networking and expects a MariaDB/Galera instance reachable via the `DB_URL` default in [`docker-compose.yml`](./docker-compose.yml). Override environment variables on the host or in the file:

```bash
export DB_URL='mysql+pymysql://user:pass@host/radius'
export JWT_SECRET_KEY='your-secret-here'
docker compose up -d
```

The following files are mounted from the host:

| Host path | Container path | Purpose |
|---|---|---|
| `./nginx/app.conf` | `/etc/nginx/conf.d/app.conf` | Nginx site config (port 8000) |
| `./user_credentials.db` | `/app/user_credentials.db` | Local SQLite auth DB |

View logs:

```bash
docker compose logs -f
```

Stop the container:

```bash
docker compose down
```

## Notes

- Success responses use `{"success": true, ...}`.
- Validation/database failures return JSON with `error` and `error_type`.
- Several query-style routes accept raw SQL fragments (`where`, `order_by`, `columns`), so use them carefully and only in trusted environments.
