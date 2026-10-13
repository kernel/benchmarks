# ClawBench task image for Harbor's Kernel environment. The browser is the
# Kernel VM this container runs in, so the image only needs ClawBench's
# runtime server, which bridges CDP and intercepts the task's final request.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq procps python3 socat \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv

WORKDIR /app/src
COPY runtime-server/ ./runtime-server/
COPY chrome-extension/ ./chrome-extension/
COPY shared/ ./shared/
COPY harbor/ ./harbor/
RUN chmod +x harbor/*.sh harbor/*.py \
    && cd runtime-server \
    && UV_PYTHON_PREFERENCE=only-system uv sync --frozen \
    && uv pip install --python .venv/bin/python fpdf2

WORKDIR /app
