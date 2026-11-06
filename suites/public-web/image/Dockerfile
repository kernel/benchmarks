FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3 \
    && rm -rf /var/lib/apt/lists/*

COPY bench/ /opt/bench/
RUN chmod +x /opt/bench/finalize-mcp /opt/bench/setup.mjs /opt/bench/score.mjs
WORKDIR /app
