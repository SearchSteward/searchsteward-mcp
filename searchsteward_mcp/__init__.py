"""SearchSteward MCP server."""

from .client import ApiError, ConfigError, SearchStewardClient

__version__ = "0.2.1"
__all__ = ["SearchStewardClient", "ApiError", "ConfigError"]
