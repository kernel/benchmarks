# Task image for retail-cart. The browser is the Kernel VM this container runs
# in, or a remote browser reached over CDP, so the image only holds the
# finalize tool, the start-URL setup and the scorer. Toolsets append their own
# layers.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3 \
    && rm -rf /var/lib/apt/lists/*

# cdp-execute drives a remote browser; it needs no browser of its own.
RUN npm install --global playwright-core@1.64.1
ENV NODE_PATH=/usr/local/lib/node_modules

COPY bench/ /opt/bench/
RUN chmod +x /opt/bench/cdp-execute /opt/bench/finalize-mcp /opt/bench/open-start-url /opt/bench/score

WORKDIR /app
