"""Shared error types and a single JSON error envelope for the API."""

from __future__ import annotations

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """A third-party provider failed in a way the client should hear about.

    Messages are written for humans and never contain provider internals,
    request URLs or credentials.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 502,
        provider: str = "routing",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.provider = provider
        self.retry_after = retry_after


def error_body(message: str, *, code: str, fields: dict | None = None) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    if fields:
        body["error"]["fields"] = fields
    return body


def api_exception_handler(exc, context):
    if isinstance(exc, ProviderError):
        body = error_body(exc.message, code=f"{exc.provider}_unavailable")
        response = Response(body, status=exc.status)
        if exc.retry_after:
            response["Retry-After"] = str(exc.retry_after)
        return response

    response = drf_exception_handler(exc, context)
    if response is not None:
        fields = None
        if isinstance(response.data, dict):
            fields = {
                key: value if isinstance(value, list) else [value]
                for key, value in response.data.items()
            }
            message = "The request could not be processed."
            if "detail" in response.data:
                message = str(response.data["detail"])
                fields = None
            else:
                message = _first_message(fields) or message
        else:
            message = str(response.data)
        response.data = error_body(message, code="invalid_request", fields=fields)
        return response

    logger.exception("Unhandled error in %s", context.get("view"))
    return Response(
        error_body("Something went wrong on our side. Please try again.", code="internal_error"),
        status=500,
    )


def _first_message(fields: dict) -> str | None:
    for messages in fields.values():
        for message in messages:
            return str(message)
    return None
