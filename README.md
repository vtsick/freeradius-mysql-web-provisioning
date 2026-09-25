# FreeRADIUS Provisioning API

This repository contains a Flask application, [`app.py`](./app.py), used to provision and manage MariaDB tables commonly used by FreeRADIUS. It exposes JSON and CSV endpoints for CRUD operations, bulk import/export, authentication, and Galera cluster monitoring. [`cluster_config.py`](./cluster_config.py) loads shared node configuration; [`container_start.py`](./container_start.py) configures the container listeners.

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

`GET /chkcluster` checks every database node in [`cluster.json`](./cluster.json)
when `CLUSTER_CONFIG` is set. Each node must have Galera enabled, be Primary,
connected, ready, and Synced (state 4). Local and cluster state UUIDs must match,
and all nodes must report the same cluster UUID and size. The reported size must
include at least all configured nodes. Set `expected_cluster_size` to enforce an
exact size, including any arbiters; the supplied `null` leaves that check disabled.
No database changes are made.

```bash
curl http://127.0.0.1:5000/chkcluster
```

A healthy response (HTTP 200) contains `healthy`, `scope: "configured_nodes"`,
`provisioning_node`, `expected_cluster_size`, `members`, and `reasons`.
Each member includes its configured `name`, `healthy`, `status_available`, `cluster_size`, raw
allowlisted `status`, and `reasons`. HTTP 503 uses the standard error envelope
with the entire aggregate under `details`, including results from available nodes
when another node fails. Connection URLs and credentials are never returned.

Checks run concurrently through small reusable pools with 3-second pool,
connection, read, and write timeouts. These are per-operation limits, not an
overall deadline; DNS resolution can take longer. Results are samples taken
at slightly different times, so membership transitions can produce a temporary
503. An arbiter has no SQL interface: exclude it from `nodes` and include it only
in `expected_cluster_size` if you want an indirect membership check.

Without `CLUSTER_CONFIG`, the original `DB_URL` single-node behavior remains,
with `scope: "connected_node"`. Like the table routes, authentication is disabled
by default; enable `@jwt_required()` when needed.

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

The supplied Compose deployment runs four containers on one Linux host using
host networking. Each targets a different MariaDB member for provisioning, while
`/chkcluster` on every HTTP port checks all four members:

| Compose service | Provisioning host / SQL port | Gunicorn (loopback) | External HTTP |
|---|---|---|---|
| `app` | `fr-1:3306` | 5000 | 8000 |
| `app-fr-2` | `fr-2:3306` | 5001 | 8001 |
| `app-fr-3` | `fr-3:3306` | 5002 | 8002 |
| `app-fr-4` | `fr-4:3306` | 5003 | 8003 |

Edit `cluster.json` for actual SQL ports and database names. These hostnames must
resolve and be reachable from the Docker host; the configuration does not create
DNS records or open firewall rules. All four entries are assumed to be MariaDB
servers, not arbiters. Galera replicates writes made through any provisioning port.

Create a local credentials file before building:

```bash
cp .env.example .env
chmod 600 .env
```

Set `DB_USER`, `DB_PASSWORD`, and `JWT_SECRET_KEY` in `.env` (ignored by Git).
Passwords are plain values, not URL-encoded. Single-quote values containing `$`
to avoid Compose interpolation. The account must be usable on each target host
for provisioning and `SHOW GLOBAL STATUS` / `SHOW GLOBAL VARIABLES`.
For different credentials per node, add `user_env` and `password_env` keys to
that node, naming variables supplied in `.env`.

Build the shared image once and start all four services:

```bash
touch user_credentials.db
docker compose build app
docker compose up -d --no-build --force-recreate
curl http://localhost:8000/chkcluster
curl 'http://localhost:8001/select/radgroupcheck?limit=1'
```

`PROVISIONING_NODE` selects the node per service. With `CLUSTER_CONFIG` enabled,
the selected node determines the provisioning connection; `DB_URL` is ignored.
Use `docker compose up -d --no-build app app-fr-2` to run only two entry points;
both still check every node in `cluster.json`. For a two-node cluster, also remove
the unused node entries and Compose services. Run no container for an arbiter.

Mounted files:

| Host path | Container path | Purpose |
|---|---|---|
| `./cluster.json` | `/app/cluster.json` | All nodes, SQL ports, and listener ports |
| `./nginx/app.conf` | `/etc/nginx/templates/app.conf.template` | Nginx template rendered on startup |
| `./user_credentials.db` | `/app/user_credentials.db` | Local SQLite auth DB |
| `/dev/log` | `/dev/log` | Host syslog socket |

After pulling Python or startup code changes, repeat the build and recreate
commands above. A restart alone does not update code copied into the image.
After editing `cluster.json` or the Nginx template, run `docker compose restart`
to reload application configuration and render the listener ports. After changing
`.env`, recreate the containers with `docker compose up -d --no-build --force-recreate`.
The Nginx template is not a directly loadable Nginx config; do not mount it over
`/etc/nginx/conf.d/app.conf`. See [deployment details](docs/nginx-deployment.md).

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
