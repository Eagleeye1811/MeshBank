# syntax=docker/dockerfile:1
# Build from the repository root:
#   docker build -f services/notification-service/Dockerfile -t meshbank/notification-service .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 meshbank \
    && useradd --system --uid 10001 --gid meshbank --no-create-home --shell /usr/sbin/nologin meshbank

WORKDIR /srv
COPY constraints.txt /tmp/constraints.txt
COPY services/notification-service/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt -c /tmp/constraints.txt

COPY shared /tmp/shared
RUN pip install --no-deps /tmp/shared && rm -rf /tmp/shared /tmp/*.txt

COPY services/notification-service/app /srv/app
# Alembic migrations: needed for DB_MIGRATION_MODE=upgrade/check at startup.
COPY services/notification-service/alembic.ini /srv/alembic.ini
COPY services/notification-service/migrations /srv/migrations
RUN mkdir -p /data && chown meshbank:meshbank /data

USER meshbank
ENV DATABASE_URL=sqlite:////data/notifications.db
VOLUME ["/data"]
EXPOSE 8006

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8006/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8006", "--no-access-log"]
