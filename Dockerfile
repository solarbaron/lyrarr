# syntax=docker/dockerfile:1

# ---- Frontend build ---------------------------------------------------------
# Runs on the build host's native platform: the output is plain JS/CSS, so
# multi-arch builds don't need to run npm under emulation.
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# `npm run build` = type-check (tsc) + vite build. Vite's outDir is
# ../lyrarr/frontend, i.e. /build/lyrarr/frontend here.
RUN npm run build

# ---- Runtime ----------------------------------------------------------------
FROM python:3.13-slim
LABEL maintainer="Lyrarr"
LABEL description="Lyrarr - Lidarr Companion for Music Metadata"
LABEL org.opencontainers.image.source="https://github.com/solarbaron/lyrarr"

# Version shown in the UI; CI passes the git tag. Empty = use the package version.
ARG LYRARR_VERSION=

# HF_HOME: Whisper/Hugging Face model downloads persist in the config volume
# instead of being re-downloaded into the container on every recreate.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    LYRARR_VERSION=${LYRARR_VERSION} \
    HF_HOME=/config/cache/huggingface

WORKDIR /app

# No system packages needed: faster-whisper decodes audio with PyAV, whose
# wheels bundle their own FFmpeg libraries.

# Exact, hash-verified dependency set (see requirements.lock header).
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Backend code (the committed lyrarr/frontend build is excluded by
# .dockerignore and replaced with the one built above).
COPY lyrarr/ ./lyrarr/
COPY --from=frontend-builder /build/lyrarr/frontend/ ./lyrarr/frontend/

COPY docker/entrypoint.sh /usr/local/bin/lyrarr-entrypoint
COPY docker/healthcheck.py /usr/local/bin/lyrarr-healthcheck

RUN chmod 0755 /usr/local/bin/lyrarr-entrypoint && mkdir -p /config

EXPOSE 6868

VOLUME ["/config", "/music"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "/usr/local/bin/lyrarr-healthcheck"]

# Runs as root unless PUID/PGID are set (see docker/entrypoint.sh).
ENTRYPOINT ["/usr/local/bin/lyrarr-entrypoint", "python", "-m", "lyrarr", "--config", "/config"]
