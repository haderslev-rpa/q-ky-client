"""
HTTPX Event Hooks for KY Client.

This module provides hook factory functions that can be used with HTTPX's
event hook system to add cross-cutting concerns like logging and error handling
to the KY client.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx


def create_response_logging_hook(
    logger: logging.Logger | None = None,
) -> Callable[[httpx.Response], None]:
    """
    Create response logging hook that captures HTTP transactions.

    Args:
        logger: Logger instance to use (defaults to module logger)

    Returns:
        Response hook function
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    def log_response(response: httpx.Response) -> None:
        """Log complete HTTP transaction from response."""
        request = response.request
        method = request.method
        url = str(request.url)
        status = response.status_code

        request_json = None
        try:
            if hasattr(request, "content") and request.content:
                request_json = _parse_json_content(request.content)
        except Exception:
            request_json = None

        response_json = None
        try:
            if not hasattr(response, "_content"):
                response.read()
            response_json = _parse_json_content(response.text)
        except Exception:
            response_json = None

        extra = {
            "event_type": "http_transaction",
            "http_method": method,
            "http_url": url,
            "http_status": status,
            "request_json": request_json,
            "response_json": response_json,
            "is_error": response.is_error,
        }

        if response.is_error:
            logger.error(f"HTTP {status}: {method} {url}", extra=extra)
        else:
            logger.info(f"HTTP {status}: {method} {url}", extra=extra)

    return log_response


def _parse_json_content(content: Any) -> Any | None:
    """
    Parse JSON content from request/response body.

    Returns parsed JSON if valid, None otherwise.
    """
    if not content:
        return None

    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            return None

    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None
