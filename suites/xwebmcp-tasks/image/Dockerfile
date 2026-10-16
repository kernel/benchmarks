# Task image for xwebmcp-tasks. The browser is the Kernel VM this container
# runs in, so the image only holds the finalize tool, the start-URL setup and
# the scorer. Toolsets append their own layers.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3 \
    && rm -rf /var/lib/apt/lists/*

COPY bench/ /opt/bench/
RUN chmod +x /opt/bench/finalize-mcp /opt/bench/open-start-url /opt/bench/score

WORKDIR /app
