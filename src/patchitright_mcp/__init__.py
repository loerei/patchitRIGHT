"""patchitRIGHT MCP Server: AST-bounded safe search-and-replace companion."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("patchitright-mcp")
except PackageNotFoundError:
    __version__ = "0.1.0"
