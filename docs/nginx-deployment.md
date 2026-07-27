# Nginx Deployment Guide

## Overview

This Flask app is a WSGI application and needs a WSGI server (Gunicorn or uWSGI) behind Nginx acting as a reverse proxy. The app itself should **not** be exposed directly to the internet.

---

## Architecture

```
Internet → Nginx (port 443) → Gunicorn/uWSGI (port 5000) → Flask app
                                                 → MariaDB
                                                 → SQLite (user_credentials.db)
```

---

## 1. Install Dependencies

```bash
# Python packages (inside your venv)
source .venv/bin/activate
pip install gunicorn flask sqlalchemy flask-jwt-extended pymysql cryptography

# Nginx
sudo apt update && sudo apt install nginx
```

Note: `pymysql` is needed if you switch the connection string from the default mysql driver. The current `app.py` uses the default `mysql://` scheme, which requires `mysqlclient` or `pymysql`. If you hit import errors, install `pymysql` and change the engine URL scheme to `mysql+pymysql://`.

---

## 2. Create a systemd Service

`/etc/systemd/system/freeradius-api.service`:

```ini
[Unit]
Description=FreeRADIUS Provisioning API
After=network.target mariadb.service
Wants=mariadb.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/path/to/flask-app
Environment="PATH=/path/to/flask-app/.venv/bin"
Environment="JWT_SECRET_KEY=replace-with-a-strong-random-secret"
Environment="DB_URL=mysql+pymysql://md:bibleblack@b2b-aaa/radius"
ExecStart=/path/to/flask-app/.venv/bin/gunicorn \
    --workers 4 \
    --bind 127.0.0.1:5000 \
    --timeout 120 \
    --max-requests 10000 \
    --max-requests-jitter 1000 \
    --access-logfile - \
    --error-logfile - \
    app:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable freeradius-api
sudo systemctl start freeradius-api
```

### Worker count formula

Set `--workers` to `(2 × CPU cores) + 1`. For a 2-core VM, use 5. Keep it at 4 for most small deployments.

---

## 3. Nginx Configuration

`/etc/nginx/sites-available/freeradius-api`:

```nginx
upstream flask_app {
    server 127.0.0.1:5000;
    keepalive 32;
}

server {
    listen 80;
    server_name api.example.com;

    # Redirect HTTP → HTTPS (recommended)
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/ssl/certs/example.com.pem;
    ssl_certificate_key /etc/ssl/private/example.com.key;

    # Security headers
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header X-XSS-Protection "1; mode=block";

    # Logs to journald via syslog
    access_log syslog:server=unix:/dev/log,facility=local6,tag=nginx,severity=info;
    error_log  syslog:server=unix:/dev/log,facility=local6,tag=nginx;

    # Increase limits for CSV uploads
    client_max_body_size 100M;
    proxy_request_buffering off;
    proxy_buffering off;

    location / {
        proxy_pass http://flask_app;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Long timeouts for CSV import endpoints
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/freeradius-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## 4. Key Configuration Details

### Upload size

The app imports CSV files (up to 100k+ rows). `client_max_body_size 100M` accommodates large files. Adjust based on your expected CSV sizes.

### Timeouts

CSV import endpoints (`/populate`, `/populate_old`, `/bulk_delete`) can take minutes for large datasets. Gunicorn's `--timeout 120` and Nginx's `proxy_read_timeout 300s` prevent premature disconnects. Increase both if you process very large files.

### Request buffering

`proxy_request_buffering off` and `proxy_buffering off` stream large uploads directly to the backend instead of buffering to disk, reducing latency and disk I/O.

### Keepalive

The `upstream keepalive 32` directive and `proxy_http_version 1.1; proxy_set_header Connection "";` enable HTTP/1.1 keepalive between Nginx and Gunicorn, reducing connection overhead.

### LOAD DATA LOCAL INFILE

If you use the `/populate/<table_name>` endpoint (LOAD DATA), ensure:

1. MariaDB has `local-infile=1` in `/etc/mysql/mariadb.conf.d/` (both `[mysqld]` and `[client]` sections).
2. The MariaDB user used by the app has `GRANT FILE ON *.* TO 'youruser'@'%';`.

---

## 5. Directory Structure & Permissions

```
/path/to/flask-app/
├── app.py
├── .venv/
├── user_credentials.db  ← created automatically, owned by www-data
└── docs/
```

The SQLite database (`user_credentials.db`) is created in the app directory. Ensure `www-data` (or whatever user runs Gunicorn) has write permission there:

```bash
sudo chown www-data:www-data /path/to/flask-app
sudo chmod 755 /path/to/flask-app
```

---

## 6. Logging — Everything via journald

All three layers ship logs to journald, visible with `journalctl`:

| Layer | Mechanism | Query |
|---|---|---|
| **Flask app** | `SysLogHandler` → `/dev/log` | `journalctl -u freeradius-api -t python` |
| **Gunicorn** | stdout/stderr → systemd | `journalctl -u freeradius-api` |
| **Nginx** | `syslog:` → `/dev/log` | `journalctl -t nginx` |

### Viewing logs

```bash
# All logs for the API service
journalctl -u freeradius-api -f

# Nginx access/error feed
journalctl -t nginx -f

# Flask application-level logs only
journalctl -u freeradius-api -t python -f

# Combined view by time
journalctl --since "5 min ago"
```

### Syslog access

Ensure the Gunicorn user can write to `/dev/log`:

```bash
sudo usermod -a -G adm www-data
```

---

## 7. Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `JWT_SECRET_KEY` | HMAC key for JWT tokens | `supersecretapikeychangeme` |
| `DB_URL` | MariaDB connection string (requires code change to read from env) | Hardcoded in `app.py:39` |

Currently the DB URL is hardcoded. If you need environment-variable control, replace line 39 in `app.py` with:

```python
engine = create_engine(
    os.environ.get('DB_URL', 'mysql+pymysql://md:bibleblack@b2b-aaa/radius'),
    ...
)
```

---

## 8. Security Considerations

- **JWT**: The `@jwt_required()` decorators are commented out in the current code. Uncomment them in production and set a strong `JWT_SECRET_KEY`.
- **Raw SQL**: Routes like `/select/<table_name>` accept raw SQL fragments (`where`, `order_by`, `columns`). This app is designed for trusted internal networks only.
- **HTTPS**: The Nginx config above redirects HTTP → HTTPS. Obtain TLS certificates via Let's Encrypt / Certbot.
- **Firewall**: Restrict port 5000 to localhost (`127.0.0.1`) so Gunicorn is not reachable from outside. The Nginx config uses `127.0.0.1:5000`.
```

