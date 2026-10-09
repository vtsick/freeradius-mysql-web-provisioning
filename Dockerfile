ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

USER root
RUN set -eu; \
    if command -v apt-get >/dev/null 2>&1; then \
        apt-get update; \
        apt-get install -y --no-install-recommends nginx; \
        rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache nginx; \
    elif command -v dnf >/dev/null 2>&1; then \
        dnf install -y nginx; \
        dnf clean all; \
    elif command -v microdnf >/dev/null 2>&1; then \
        microdnf install -y nginx; \
        microdnf clean all; \
    elif command -v yum >/dev/null 2>&1; then \
        yum install -y nginx; \
        yum clean all; \
    else \
        echo 'Unsupported base image: apt-get, apk, dnf, microdnf, or yum is required' >&2; \
        exit 1; \
    fi

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app.py cluster_config.py container_start.py ./
COPY nginx/app.conf /etc/nginx/templates/app.conf.template
COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /entrypoint.sh
RUN mkdir -p /etc/nginx/conf.d /run && chmod +x /entrypoint.sh

EXPOSE 8000 5000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["--workers", "9", \
     "--timeout", "600", \
     "--max-requests", "10000", \
     "--max-requests-jitter", "1000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
