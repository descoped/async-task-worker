"""
Cache adapter for AsyncTaskWorker to use async_cache.

Provides a clean integration between AsyncTaskWorker and AsyncCache.
"""

import logging
from typing import Any, Dict, Tuple, Optional, Callable

from async_cache import AsyncCache
from async_cache.adapters import MemoryCacheAdapter

logger = logging.getLogger(__name__)


class AsyncCacheAdapter:
    """
    Adapter that provides a bridge between AsyncTaskWorker and async_cache package.
    
    This adapter translates between task-specific concepts in AsyncTaskWorker and 
    the more generic caching concepts in AsyncCache.
    """

    def __init__(
            self,
            default_ttl: Optional[int] = None,
            enabled: bool = True,
            max_serialized_size: int = 10 * 1024 * 1024,
            validate_keys: bool = False,
            cleanup_interval: int = 900,
            max_size: Optional[int] = 1000
    ):
        """
        Initialize the cache adapter.
        
        Args:
            default_ttl: Default time-to-live in seconds (None for no expiry)
            enabled: Whether caching is enabled by default
            max_serialized_size: Maximum size in bytes for serialized objects
            validate_keys: Whether to validate generated cache keys
            cleanup_interval: Interval in seconds for automatic cleanup of stale mappings
            max_size: Maximum number of items to store in memory cache (None for unlimited)
        """
        # Create memory adapter
        self._adapter = MemoryCacheAdapter(max_size=max_size)

        # Create AsyncCache instance
        self._cache = AsyncCache(
            adapter=self._adapter,
            default_ttl=default_ttl,
            enabled=enabled,
            max_serialized_size=max_serialized_size,
            validate_keys=validate_keys,
            cleanup_interval=cleanup_interval
        )

    async def start_cleanup_task(self) -> None:
        """Start the periodic cleanup task."""
        await self._cache.start_cleanup_task()

    async def stop_cleanup_task(self) -> None:
        """Stop the periodic cleanup task."""
        await self._cache.stop_cleanup_task()

    async def get(
            self,
            func_name: str,
            args: tuple,
            kwargs: dict,
            cache_key_fn: Optional[Callable] = None,
            task_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Any]:
        """
        Get cached result for a task.
        
        Args:
            func_name: Name of the task function
            args: Positional arguments
            kwargs: Keyword arguments
            cache_key_fn: Optional custom key generation function
            task_id: Optional task ID for reverse mapping
            metadata: Optional metadata for custom key generation
            
        Returns:
            Tuple of (cache_hit, result)
        """
        # Map task_id to entry_id for async_cache
        return await self._cache.get(
            func_name, args, kwargs,
            cache_key_fn=cache_key_fn,
            entry_id=task_id,
            metadata=metadata
        )

    async def set(
            self,
            func_name: str,
            args: tuple,
            kwargs: dict,
            result: Any,
            ttl: Optional[int] = None,
            cache_key_fn: Optional[Callable] = None,
            task_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Store result in cache.
        
        Args:
            func_name: Name of the task function
            args: Positional arguments
            kwargs: Keyword arguments
            result: Result to cache
            ttl: Time-to-live override (uses default if None)
            cache_key_fn: Optional custom key generation function
            task_id: Optional task ID for reverse mapping
            metadata: Optional metadata for custom key generation
            
        Returns:
            True if successfully cached, False otherwise
        """
        # Map task_id to entry_id for async_cache
        return await self._cache.set(
            func_name, args, kwargs, result,
            ttl=ttl,
            cache_key_fn=cache_key_fn,
            entry_id=task_id,
            metadata=metadata
        )

    async def invalidate(
            self,
            func_name: str,
            args: tuple,
            kwargs: dict,
            cache_key_fn: Optional[Callable] = None,
            task_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Invalidate a specific cache entry.
        
        Args:
            func_name: Name of the task function
            args: Positional arguments
            kwargs: Keyword arguments
            cache_key_fn: Optional custom key generation function
            task_id: Optional task ID for lookup
            metadata: Optional metadata for custom key generation
            
        Returns:
            True if invalidated, False if not found
        """
        return await self._cache.invalidate(
            func_name, args, kwargs,
            cache_key_fn=cache_key_fn,
            entry_id=task_id,
            metadata=metadata
        )

    async def invalidate_by_task_id(self, task_id: str) -> bool:
        """
        Invalidate a cache entry using a task ID.
        
        Args:
            task_id: Task ID associated with the cache entry
            
        Returns:
            True if invalidated, False if not found
        """
        return await self._cache.invalidate_by_id(task_id)

    async def clear(self) -> None:
        """Clear all cached results."""
        await self._cache.clear()

    @property
    def enabled(self) -> bool:
        """Get cache enabled status."""
        return self._cache.enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Set cache enabled status."""
        self._cache.enabled = value

    @property
    def default_ttl(self) -> Optional[int]:
        """Get default TTL setting."""
        return self._cache.default_ttl

    @default_ttl.setter
    def default_ttl(self, value: Optional[int]) -> None:
        """Set default TTL setting."""
        self._cache.default_ttl = value

    @property
    def cleanup_interval(self) -> int:
        """Get cleanup interval setting."""
        return self._cache.cleanup_interval

    @cleanup_interval.setter
    def cleanup_interval(self, value: int) -> None:
        """Set cleanup interval setting."""
        self._cache.cleanup_interval = value
