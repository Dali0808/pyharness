FROM node:24.19.0-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.1 AS uv
FROM python:3.11-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY ai ./ai
COPY agent ./agent
COPY coding_agent ./coding_agent
RUN uv sync --locked --no-dev && \
    groupadd --gid 10001 lario && useradd --uid 10001 --gid 10001 --no-create-home lario && \
    mkdir /data /backups && chown 10001:10001 /data /backups
COPY --from=frontend /build/dist ./frontend/dist
ARG RELEASE=development
ENV LARIO_RELEASE=$RELEASE LARIO_DATA_DIR=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"
USER 10001:10001
EXPOSE 8000
CMD ["lario-server", "serve", "--host", "0.0.0.0", "--port", "8000"]
