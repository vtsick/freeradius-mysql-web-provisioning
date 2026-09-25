# Nginx and Cluster Deployment

## Request Routing

The supplied Docker Compose configuration runs one provisioning entry point per
MariaDB member on a single Linux host, with `network_mode: host`:

```text
host:8000 -> Nginx -> 127.0.0.1:5000 -> Gunicorn -> fr-1:3306
host:8001 -> Nginx -> 127.0.0.1:5001 -> Gunicorn -> fr-2:3306
host:8002 -> Nginx -> 127.0.0.1:5002 -> Gunicorn -> fr-3:3306
host:8003 -> Nginx -> 127.0.0.1:5003 -> Gunicorn -> fr-4:3306
```

These are defaults from `cluster.json`, not Docker port translations. Each
container has its own Nginx and Gunicorn processes. Gunicorn binds only to
loopback; Nginx binds all IPv4 interfaces. All HTTP and Gunicorn ports must be
unique on the shared host. Ensure the configured ports are unused before startup.

Each container selects its provisioning database using `PROVISIONING_NODE`.
All `/chkcluster` requests check every SQL node in the shared configuration,
regardless of the HTTP port used. Provisioning writes replicate through Galera.

## Configuration and Startup

1. Edit `cluster.json`: node names, resolvable hostnames, SQL ports, database names,
   Gunicorn ports, and external HTTP ports. Configure 2 to 4 SQL nodes. Do not
   list an arbiter as a SQL node. Optionally set `expected_cluster_size` to the
   total membership including arbiters; `null` disables exact-size enforcement.
2. Copy `.env.example` to `.env`, restrict its permissions, and enter real database
   credentials and a strong JWT secret. Credentials are not included in the image.
3. Ensure `fr-1` through `fr-4` resolve on the Docker host and TCP 3306 (or the
   configured SQL port) is reachable. Host networking uses the host's network,
   but arbitrary host `/etc/hosts` aliases are not automatically copied into
   containers. Use DNS or Compose `extra_hosts` on the shared `x-app` definition
   when needed. Do not invent IP mappings.
4. Ensure the SQLite file exists with `touch user_credentials.db`. Preserve an
   existing file. For a new installation, initialize its users table after startup
   with `docker compose exec app python -c 'from app import init_sqlite_db; init_sqlite_db()'`.

```bash
docker compose build app
docker compose up -d --no-build --force-recreate
docker compose ps
docker compose logs --tail=100
```

The image is shared by all services. `app` is retained as the first service name
for compatibility with previous deployments. To use fewer entry points, start
only the selected services, or remove unused services from Compose. If a node is
removed from the cluster configuration, also remove its provisioning service.

`entrypoint.sh` removes the distribution's default Nginx site and invokes
`container_start.py`. The startup script reads the selected node, replaces only
`${GUNICORN_PORT}` and `${HTTP_PORT}` in the Nginx template, validates it using
`nginx -t`, starts Nginx, and executes Gunicorn with the same configured bind port.
Nginx variables such as `$host` and `$remote_addr` remain intact.

The default Gunicorn arguments use nine synchronous workers per container and a
600-second worker timeout. Four containers therefore start 36 workers; review
worker counts and database connection capacity for the deployment host. Set
Compose `command` to a Gunicorn argument list to tune workers, retaining `app:app`.
The startup script owns `--bind`; set listening ports only in `cluster.json`.

## Updating

Python code, dependencies, and startup scripts are copied into the image. After
pulling code changes, rebuild and recreate using the commands above. A failed
Docker Hub image download means no new image was built.

`cluster.json` and `nginx/app.conf` are mounted read-only. The latter is now a
template mounted at `/etc/nginx/templates/app.conf.template`, not a finished
configuration. After editing either file, restart all services:

```bash
docker compose restart
```

This regenerates `/etc/nginx/conf.d/app.conf` and reloads the Python configuration.
Changing `.env` requires recreation, not just restarting. Do not retain the old
mount at `/etc/nginx/conf.d/app.conf` when migrating from the single-instance setup.

## Validation and Troubleshooting

```bash
curl -i http://localhost:8000/hello
curl -i http://localhost:8000/chkcluster
curl -i 'http://localhost:8001/select/radgroupcheck?limit=1'
docker compose exec app nginx -T
docker compose logs --tail=100 app
```

A 502 on `/hello` means Nginx could not obtain a valid Gunicorn response; inspect
Gunicorn startup logs and check the loopback endpoint configured for that service.
A 503 JSON response on `/chkcluster` includes per-member health diagnostics under
`details.members`. A 404 after updating code usually indicates an old image is
still running. Check different services using their own HTTP ports.

Nginx and Flask send logs to the mounted host `/dev/log` socket. Gunicorn writes
to container stdout/stderr. Host syslog routing determines whether Nginx messages
appear in `journalctl -t nginx` or another log destination.

## Proxy Behavior and Access

Nginx forwards every path, including query strings, and supplies `Host`,
`X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto`. It allows request bodies up
to 100 MB, disables request and response buffering, and uses 300-second proxy
read/send timeouts. Flask does not automatically trust forwarded headers.

This configuration exposes plain HTTP without proxy authentication. JWT route
decorators remain disabled by default. Restrict access to trusted networks,
or configure authentication and TLS before exposing provisioning endpoints more
broadly. Security headers do not provide authentication. For HTTPS, adapt the
template with certificate mounts and TLS listeners, or put a TLS proxy in front.

For a non-container deployment, generate a concrete Nginx configuration from the
template with matching ports, run Gunicorn on loopback, and set `CLUSTER_CONFIG`
and `PROVISIONING_NODE` for each instance. Without `CLUSTER_CONFIG`, the app uses
the legacy `DB_URL` connection and the container startup defaults to 5000/8000.
