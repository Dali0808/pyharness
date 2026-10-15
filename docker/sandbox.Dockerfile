FROM python:3.11-slim

RUN pip install --no-cache-dir uv
WORKDIR /opt/pyharness
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

ENV PATH="/opt/pyharness/.venv/bin:${PATH}"
USER 65534:65534
WORKDIR /workspace
