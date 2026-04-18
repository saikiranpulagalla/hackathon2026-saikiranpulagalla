"""Custom exception hierarchy for mock tools."""


class ToolBaseError(Exception):
    """Base exception for all tool errors."""
    pass


class ToolTimeoutError(ToolBaseError):
    """Raised when a tool call exceeds the timeout threshold."""
    pass


class ToolError(ToolBaseError):
    """Raised for transient or permanent server-side errors."""

    def __init__(self, message: str, is_transient: bool = True):
        super().__init__(message)
        self.is_transient = is_transient
