"""Retry wrapper with exponential backoff for tool calls."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional, Type

from pydantic import BaseModel, ValidationError

from .exceptions import ToolBaseError, ToolError, ToolTimeoutError


class ToolResult(BaseModel):
    """Result of a tool call attempt, always returned (never raises)."""
    tool_name: str
    success: bool
    validated_data: Optional[Any] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    attempt: int
    duration_ms: float
    recoverable: Optional[bool] = None

    model_config = {"arbitrary_types_allowed": True}


async def retry_with_backoff(
    tool_fn: callable,
    args: dict,
    schema: Type[BaseModel],
    tool_name: str = "",
    max_retries: int = 3,
    base_delay: float = 0.5,
) -> ToolResult:
    """
    Call a tool function with retry and exponential backoff.

    - ToolTimeoutError → recoverable, retry
    - ToolError (transient) → recoverable, retry
    - ValidationError → non-recoverable, no retry (deterministic)
    - Always returns ToolResult (never raises)
    """
    if not tool_name:
        tool_name = getattr(tool_fn, "__name__", "unknown")

    attempt = 0
    error_type = None
    error_message = None
    recoverable = None
    start_time = time.monotonic()

    while attempt < max_retries:
        attempt_start = time.monotonic()
        try:
            raw_response = await tool_fn(**args)

            # Handle list responses (e.g., search_knowledge_base)
            if isinstance(raw_response, list):
                validated = [schema.model_validate(item) for item in raw_response]
            else:
                validated = schema.model_validate(raw_response)

            duration_ms = (time.monotonic() - attempt_start) * 1000

            return ToolResult(
                tool_name=tool_name,
                success=True,
                validated_data=validated,
                attempt=attempt + 1,
                duration_ms=duration_ms,
            )

        except ToolTimeoutError as e:
            error_type = "timeout"
            error_message = str(e)
            recoverable = True

        except ToolError as e:
            error_type = "error"
            error_message = str(e)
            recoverable = e.is_transient

        except ValidationError as e:
            error_type = "validation"
            error_message = str(e)[:500]  # Truncate long validation errors
            recoverable = False

        except Exception as e:
            error_type = "unexpected"
            error_message = str(e)
            recoverable = False

        attempt += 1

        if not recoverable or attempt >= max_retries:
            break

        # Exponential backoff with jitter
        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.1)
        await asyncio.sleep(delay)

    duration_ms = (time.monotonic() - start_time) * 1000

    return ToolResult(
        tool_name=tool_name,
        success=False,
        error_type=error_type,
        error_message=error_message,
        attempt=attempt,
        duration_ms=duration_ms,
        recoverable=recoverable,
    )
