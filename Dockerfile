FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py cluster_config.py container_start.py ./
COPY nginx/app.conf /etc/nginx/templates/app.conf.template
COPY entrypoint.sh /entrypoint.sh

EXPOSE 8000 5000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["--workers", "9", \
     "--timeout", "600", \
     "--max-requests", "10000", \
     "--max-requests-jitter", "1000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
