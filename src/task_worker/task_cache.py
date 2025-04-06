"""
Task Cache System

This module provides caching functionality for the AsyncTaskWorker
to store and retrieve task results.
"""

import hashlib
import json
import logging
import pickle
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class CacheAdapter(ABC):
    """
    Abstract base class for cache adapters.
    Implementations should provide concrete storage mechanisms.
    """

    @abstractmethod
    async def get(self, key: str) -> Tuple[bool, Any]:
        """
        Get a value from the cache.

        Args:
            key: The cache key

        Returns:
            Tuple of (hit, value) where hit is True if item was in cache
        """
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set a value in the cache.

        Args:
            key: The cache key
            value: The value to store
            ttl: Optional time-to-live in seconds
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """
        Delete a value from the cache.

        Args:
            key: The cache key

        Returns:
            True if deleted, False if not found
        """
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all items from the cache."""
        pass


class MemoryCacheAdapter(CacheAdapter):
    """
    Simple in-memory cache adapter.
    Supports TTL and max size eviction policies.
    """

    def __init__(self, max_size: Optional[int] = 1000):
        """
        Initialize memory cache.

        Args:
            max_size: Maximum number of items to store (None for unlimited)
        """
        self._cache: Dict[str, Tuple[Any, Optional[float]]] = {}  # key -> (value, expiry)
        self._access_times: Dict[str, float] = {}  # key -> last access timestamp
        self.max_size = max_size

    async def get(self, key: str) -> Tuple[bool, Any]:
        """Get a value from the cache with TTL support."""
        if key not in self._cache:
            return False, None

        value, expiry = self._cache[key]
        current_time = time.time()

        # Update access time for LRU to current timestamp
        self._access_times[key] = current_time

        # Check if expired
        if expiry is not None and current_time > expiry:
            # Remove expired item
            del self._cache[key]
            del self._access_times[key]
            return False, None

        return True, value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set a value in the cache with TTL support."""
        # Calculate expiry time if TTL provided
        expiry = None
        if ttl is not None:
            expiry = time.time() + ttl

        # Evict if at max capacity and adding a new key
        if self.max_size is not None and len(self._cache) >= self.max_size and key not in self._cache:
            self._evict_one()

        # Store value and update access time to current timestamp
        self._cache[key] = (value, expiry)
        self._access_times[key] = time.time()

    async def delete(self, key: str) -> bool:
        """Delete a value from the cache."""
        if key in self._cache:
            del self._cache[key]
            del self._access_times[key]
            return True
        return False

    async def clear(self) -> None:
        """Clear all items from the cache."""
        self._cache.clear()
        self._access_times.clear()

    def _evict_one(self) -> None:
        """Evict the least recently used item from the cache."""
        if not self._access_times:
            return

        # Find oldest accessed key (lowest timestamp value)
        oldest_key = min(self._access_times.items(), key=lambda x: x[1])[0]

        # Remove from cache
        del self._cache[oldest_key]
        del self._access_times[oldest_key]
        logger.debug(f"Evicted cache item: {oldest_key}")


class TaskCache:
    """
    Task cache manager for AsyncTaskWorker.
    Handles cache key generation and serialization.
    """

    def __init__(
            self,
            adapter: CacheAdapter,
            default_ttl: Optional[int] = None,
            enabled: bool = True
    ):
        """
        Initialize the task cache.

        Args:
            adapter: Cache adapter implementation
            default_ttl: Default time-to-live in seconds (None for no expiry)
            enabled: Whether caching is enabled by default
        """
        self.adapter = adapter
        self.default_ttl = default_ttl
        self.enabled = enabled

    @staticmethod
    def generate_key(func_name: str, args: tuple, kwargs: dict) -> str:
        """
        Generate a cache key from function name and arguments.

        Args:
            func_name: Name of the task function
            args: Positional arguments
            kwargs: Keyword arguments

        Returns:
            Cache key string
        """
        # Serialize arguments
        try:
            # Try JSON first for better readability and performance
            key_parts = [func_name, json.dumps(args), json.dumps(kwargs, sort_keys=True)]
            key_string = ":".join(key_parts)
        except (TypeError, ValueError):
            # Fall back to pickle for non-JSON-serializable objects
            try:
                pickled = pickle.dumps((args, kwargs))
                key_string = f"{func_name}:{hashlib.md5(pickled).hexdigest()}"
            except Exception:
                # Last resort - use string representation
                key_string = f"{func_name}:{str(args)}:{str(sorted(kwargs.items()))}"

        # Create a hash for the final key
        return hashlib.md5(key_string.encode()).hexdigest()

    async def get(self, func_name: str, args: tuple, kwargs: dict) -> Tuple[bool, Any]:
        """
        Get cached result for a task.

        Args:
            func_name: Name of the task function
            args: Positional arguments
            kwargs: Keyword arguments

        Returns:
            Tuple of (cache_hit, result)
        """
        if not self.enabled:
            return False, None

        key = self.generate_key(func_name, args, kwargs)
        return await self.adapter.get(key)

    async def set(
            self,
            func_name: str,
            args: tuple,
            kwargs: dict,
            result: Any,
            ttl: Optional[int] = None
    ) -> None:
        """
        Store result in cache.

        Args:
            func_name: Name of the task function
            args: Positional arguments
            kwargs: Keyword arguments
            result: Result to cache
            ttl: Time-to-live override (uses default if None)
        """
        if not self.enabled:
            return

        key = self.generate_key(func_name, args, kwargs)
        effective_ttl = ttl if ttl is not None else self.default_ttl
        await self.adapter.set(key, result, effective_ttl)

    async def invalidate(self, func_name: str, args: tuple, kwargs: dict) -> bool:
        """
        Invalidate a specific cache entry.

        Args:
            func_name: Name of the task function
            args: Positional arguments
            kwargs: Keyword arguments

        Returns:
            True if invalidated, False if not found
        """
        key = self.generate_key(func_name, args, kwargs)
        return await self.adapter.delete(key)

    async def clear(self) -> None:
        """Clear all cached results."""
        await self.adapter.clear()
