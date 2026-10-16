# xwebmcp toolset. Appended after kernel-mcp-local's layer: the site's tool
# files and the scripts that register and check them.
COPY xwebmcp/tools.json /opt/xwebmcp/tools.json
COPY xwebmcp/tools/ /opt/xwebmcp/tools/
COPY xwebmcp/bin/ /usr/local/bin/
