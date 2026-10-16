COPY playwright-mcp/bin/ /usr/local/bin/
ENTRYPOINT ["/usr/local/bin/playwright-mcp-entrypoint"]
WORKDIR /app
