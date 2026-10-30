"""SearchSteward MCP server."""

from .client import ApiError, ConfigError, SearchStewardClient

__version__ = "0.6.0"
__all__ = ["SearchStewardClient", "ApiError", "ConfigError"]
