# syntax=docker/dockerfile:1.6
#
# Multi-stage Dockerfile for obsidian-rag-api.
#
# Stage 1 ("builder") creates a virtualenv with all Python dependencies.
# Stage 2 ("runtime") is a slim image with only what is needed at runtime.
#
# The Obsidian Headless binary is intentionally NOT vendored here. Operators
# who want full Obsidian sync should base their image on this one and add
# the binary to /usr/local/bin (its presence is auto-detected at startup).

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    OBSIDIAN_VAULT_PATH=/vault \
    SQLITE_PATH=/data/obsidian-rag-api.sqlite

# Run as a non-root user.
RUN groupadd --system app \
 && useradd --system --gid app --home /app --shell /usr/sbin/nologin app \
 && mkdir -p /vault /data \
 && chown -R app:app /vault /data

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
