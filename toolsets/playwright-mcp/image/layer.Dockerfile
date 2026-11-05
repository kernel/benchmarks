# playwright-mcp toolset. Appended to the suite's Dockerfile, which must have
# Node.js. The toolset appends the line installing its @playwright/mcp version.
COPY playwright-mcp/bin/ /usr/local/bin/
