# Build frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Python backend
FROM python:3.13-slim
LABEL maintainer="Lyrarr"
LABEL description="Lyrarr - Lidarr Companion for Music Metadata"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY lyrarr/ ./lyrarr/

# Copy built frontend
COPY --from=frontend-builder /build/dist/ ./lyrarr/frontend/

# Create data directory
RUN mkdir -p /config

EXPOSE 6868

VOLUME ["/config", "/music"]

ENTRYPOINT ["python", "-m", "lyrarr", "--config_dir", "/config"]
