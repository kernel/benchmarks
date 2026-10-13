# kernel-mcp-local toolset. Appended to the suite's Dockerfile, which must be
# Debian-based with Node.js and Python 3.
RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-server \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global bun@1.3.3 mcp-remote@0.1.38

WORKDIR /opt/kernel-mcp-server
COPY kernel-mcp-local/source/package.json kernel-mcp-local/source/bun.lock ./
RUN bun install --frozen-lockfile
COPY kernel-mcp-local/source/ ./
# The server is only reached with a project API key, so Clerk gets placeholders.
ENV CLERK_SECRET_KEY=sk_test_kernel_mcp_benchmark_local_only \
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_YmVuY2htYXJrLmNsZXJrLmFjY291bnRzLmRldiQ \
    KERNEL_CLI_PROD_CLIENT_ID=kernel-mcp-benchmark \
    KERNEL_CLI_STAGING_CLIENT_ID=kernel-mcp-benchmark \
    KERNEL_CLI_DEV_CLIENT_ID=kernel-mcp-benchmark \
    KERNEL_MCP_ENABLED_TOOLSETS=playwright \
    REDIS_URL=redis://127.0.0.1:6379 \
    NEXT_TELEMETRY_DISABLED=1
RUN bun run build
COPY kernel-mcp-local/bin/ /usr/local/bin/
ENTRYPOINT ["/usr/local/bin/kernel-mcp-entrypoint"]
WORKDIR /app
