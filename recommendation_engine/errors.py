"""Exceptions raised by the recommendation engine (framework-independent)."""


class RecommendationError(Exception):
    """Base class for all recommendation engine errors."""


class ConfigError(RecommendationError):
    """The scoring configuration is missing, malformed or inconsistent."""


class InvalidRequestError(RecommendationError, ValueError):
    """A caller passed an invalid argument (empty customer ID, limit out of range, ...)."""


class CustomerNotFoundError(RecommendationError, LookupError):
    """The customer does not exist in the data source."""

    def __init__(self, customer_id: str):
        super().__init__(f"Customer '{customer_id}' was not found.")
        self.customer_id = customer_id


class DataSourceError(RecommendationError):
    """The data source (database) could not be read."""
