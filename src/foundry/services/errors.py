"""The one error services raise; the API turns it into JSON {"error", "field"?} with its status."""

from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, status: int, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.message, self.field = status, message, field  # field: the form field it belongs to
