"""Domain exceptions. The API layer maps these to HTTP responses."""


class GeoServiceError(Exception):
    """Base class for all expected (non-bug) errors raised by the service."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UnsupportedFileTypeError(GeoServiceError):
    status_code = 415
    code = "unsupported_file_type"


class FileTooLargeError(GeoServiceError):
    status_code = 413
    code = "file_too_large"


class InvalidGeoFileError(GeoServiceError):
    """The upload has an accepted type but its contents cannot be read."""

    status_code = 422
    code = "invalid_geo_file"


class FileRecordNotFoundError(GeoServiceError):
    status_code = 404
    code = "not_found"


class FileNotReadyError(GeoServiceError):
    status_code = 409
    code = "file_not_ready"
