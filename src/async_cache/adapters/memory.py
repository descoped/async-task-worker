"""
In-memory cache adapter for the async_cache package.

Provides a simple in-memory caching implementation with LRU eviction policy.
"""

import asyncio
import time
from typing import Any, Dict, Optional, Tuple

from async_cache.core import CacheAdapter


class MemoryCacheAdapter(CacheAdapter):
    """
    Simple in-memory cache adapter.
    Supports TTL and max size eviction policies with LRU (Least Recently Used) replacement.
    Thread-safe implementation with asyncio locks.
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
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Tuple[bool, Any]:
        """
        Get a value from the cache with TTL support.
        
        Args:
            key: The cache key
            
        Returns:
            Tuple of (hit, value) where hit is True if the item was in cache and not expired
        """
        async with self._lock:
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
        """
        Set a value in the cache with TTL support.
        
        Args:
            key: The cache key
            value: The value to store
            ttl: Optional time-to-live in seconds
        """
        async with self._lock:
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
        """
        Delete a value from the cache.
        
        Args:
            key: The cache key
            
        Returns:
            True if deleted, False if not found
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                del self._access_times[key]
                return True
            return False

    async def clear(self) -> None:
        """Clear all items from the cache."""
        async with self._lock:
            self._cache.clear()
            self._access_times.clear()

    def _evict_one(self) -> None:
        """
        Evict the least recently used item from the cache.
        
        Note: This method should only be called with the lock held.
        """
        if not self._access_times:
            return

        # Find oldest accessed key (lowest timestamp value)
        oldest_key = min(self._access_times.items(), key=lambda x: x[1])[0]

        # Remove from cache
        del self._cache[oldest_key]
        del self._access_times[oldest_key]
