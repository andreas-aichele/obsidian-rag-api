# syntax=docker/dockerfile:1.6
#
# Multi-stage Dockerfile for obsidian-rag-api.
#
# Stage 1 ("builder") creates a virtualenv with all Python dependencies.
# Stage 2 ("runtime") is a slim image with only what is needed at runtime.
#
# The official Obsidian Headless CLI (`ob`, https://github.com/obsidianmd/obsidian-headless)
# is installed via `npm install -g obsidian-headless` by default. Set the
# build arg INSTALL_OBSIDIAN_HEADLESS=false to skip that install (and the
# Node 22 runtime that backs it) when the vault is populated by other means.

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

# Optionally install the official Obsidian Headless CLI (`ob`). It ships
# the `better-sqlite3` native module, so we briefly add build tools for
# the rare case prebuilt binaries are unavailable for the target arch,
# then strip them again to keep the image lean.
ARG INSTALL_OBSIDIAN_HEADLESS=true
ARG OBSIDIAN_HEADLESS_VERSION=latest
RUN if [ "$INSTALL_OBSIDIAN_HEADLESS" = "true" ]; then \
        set -eux; \
        apt-get update; \
        apt-get install -y --no-install-recommends curl ca-certificates gnupg; \
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash -; \
        apt-get install -y --no-install-recommends nodejs make g++; \
        npm install -g --omit=dev "obsidian-headless@${OBSIDIAN_HEADLESS_VERSION}"; \
        apt-get purge -y --auto-remove curl gnupg make g++; \
        rm -rf /var/lib/apt/lists/* /root/.npm; \
    fi

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
