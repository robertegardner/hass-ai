class HAError(Exception):
    """Base for all Home Assistant client errors."""


class HAAuthError(HAError):
    """Token was rejected. Fatal — never retried, to avoid HA-side lockout."""


class HAConnectionError(HAError):
    """Transient connection failure; the supervisor retries these."""


class ReadOnlyViolation(HAError):
    """A write to Home Assistant was attempted while read-only mode is active."""
