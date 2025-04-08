"""Exceptions for the async_cache package."""


class CacheError(Exception):
    """Base exception for all cache-related errors."""
    pass


class SerializationError(CacheError):
    """Exception raised when serialization or deserialization fails."""
    pass


class InvalidCacheKeyError(CacheError):
    """Exception raised when a cache key is invalid."""
    pass
