"""Tibber API response handler"""

import logging
from http import HTTPStatus
from json import JSONDecodeError
from typing import Any

from aiohttp import ClientResponse

from .const import (
    API_ERR_CODE_UNAUTH,
    API_ERR_CODE_UNKNOWN,
    HTTP_CODES_FATAL,
    HTTP_CODES_RETRIABLE,
)
from .exceptions import (
    FatalHttpExceptionError,
    InvalidLoginError,
    NotForDemoUserError,
    RetryableHttpExceptionError,
)

_LOGGER = logging.getLogger(__name__)


def extract_error_details(errors: list[dict], default_message: str) -> tuple[str, str]:
    """Extracts error code and message from the first error entry."""

    if not errors:
        return API_ERR_CODE_UNKNOWN, default_message
    error: dict[str, Any] = errors[0]
    extensions: dict[str, Any] = error.get("extensions", {})
    return (
        extensions.get("code", API_ERR_CODE_UNKNOWN),
        error.get("message", default_message),
    )


async def extract_response_data(response: ClientResponse) -> dict[str, Any]:
    """Extracts the response as JSON or throws a HttpException"""

    _LOGGER.debug("Response status: %s", response.status)

    if response.content_type != "application/json":
        raise FatalHttpExceptionError(
            response.status,
            f"Unexpected content type: {response.content_type}",
            API_ERR_CODE_UNKNOWN,
        )

    try:
        result: dict[str, Any] = await response.json()
    except JSONDecodeError as err:
        raise FatalHttpExceptionError(
            response.status,
            f"Failed to parse response: {err}",
            API_ERR_CODE_UNKNOWN,
        ) from err

    if not isinstance(result, dict):
        raise FatalHttpExceptionError(
            response.status,
            f"Unexpected response: {result}",
            API_ERR_CODE_UNKNOWN,
        )

    # precache errors
    errors: list[dict[str, Any]] = result.get("errors", [])

    # From API Changelog 2025-01-03:
    # As part of a major framework update, the API will now no longer return an HTTP status code 400
    # on authentication errors (missing or invalid tokens). Instead, it will follow the convention of
    # several GraphQL servers and return a 200 status code, and the errors array will contain an
    # object whose extensions.code property will have the value UNAUTHENTICATED set.

    if response.status == HTTPStatus.OK:
        if not errors:
            return result

        error_code, error_message = extract_error_details(errors, str(response.content))
        if error_code == "UNAUTHENTICATED":
            _LOGGER.error("InvalidLoginError %s %s", error_message, error_code)
            raise InvalidLoginError(response.status, error_message, error_code)

        if (error_code == "INTERNAL_SERVER_ERROR") & ("demo user" in error_message):
            _LOGGER.error("NotForDemoUserError %s %s", error_message, error_code)
            raise NotForDemoUserError(response.status, error_message, error_code)

    if response.status in HTTP_CODES_RETRIABLE:
        error_code, error_message = extract_error_details(errors, str(response.content))
        _LOGGER.error("RetryableHttpExceptionError %s %s", error_message, error_code)
        raise RetryableHttpExceptionError(response.status, message=error_message, extension_code=error_code)

    if response.status in HTTP_CODES_FATAL:
        error_code, error_message = extract_error_details(errors, default_message="request failed")
        if error_code == API_ERR_CODE_UNAUTH:
            _LOGGER.error("InvalidLoginError %s %s", error_message, error_code)
            raise InvalidLoginError(response.status, error_message, error_code)

        _LOGGER.error("FatalHttpExceptionError %s %s", error_message, error_code)
        raise FatalHttpExceptionError(response.status, error_message, error_code)

    error_code, error_message = extract_error_details(errors, default_message="N/A")

    # if reached here the HTTP response code is not currently handled
    _LOGGER.error("FatalHttpExceptionError %s %s", error_message, error_code)
    raise FatalHttpExceptionError(response.status, f"Unhandled error: {error_message}", error_code)
