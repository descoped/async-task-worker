"""
Core components for the async_cache package.

Provides the CacheAdapter abstract base class and AsyncCache for caching operations.
"""

import asyncio
import contextlib
import hashlib
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple, Set, Iterator

from async_cache.exceptions import SerializationError, InvalidCacheKeyError
from async_cache.key_utils import CacheKeyContext, CacheKeyFn
from async_cache.serialization import MsgPackSerializer

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


class AsyncCache:
    """
    Async cache manager with customizable key generation.
    
    Features:
    - Supports multiple cache backends through adapter interface
    - Thread-safe and async-ready 
    - Customizable key generation
    - Automatic cleanup of stale mappings
    - TTL support
    - Comprehensive serialization
    
    Examples:
        Basic usage:
        
        ```python
        # Create a cache with memory adapter
        cache = AsyncCache(MemoryCacheAdapter())
        
        # Store a result in the cache
        await cache.set("my_function", args=(1, 2), kwargs={"a": "b"}, result="result")
        
        # Retrieve from cache
        hit, value = await cache.get("my_function", args=(1, 2), kwargs={"a": "b"})
        ```
        
        With custom key function:
        
        ```python
        # Define a custom key function based on version
        def version_key(context):
            return f"v{context['metadata']['version']}"
            
        # Store with custom key function and metadata
        await cache.set("get_user", args=(), kwargs={}, result="result",
                       metadata={"version": "1.2"}, cache_key_fn=version_key)
        ```
        
        With composed key functions:
        
        ```python
        # Create component extractors
        user_key = extract_key_component("kwargs.user_id")
        version_key = extract_key_component("metadata.version")
        
        # Compose them into a single key function
        combined_key = compose_key_functions(user_key, version_key)
        
        # Use the composite key function
        await cache.set("get_data", args=(), kwargs={"user_id": 123}, 
                       result="result", metadata={"version": "1.0"},
                       cache_key_fn=combined_key)
        ```
    """

    def __init__(
            self,
            adapter: CacheAdapter,
            default_ttl: Optional[int] = None,
            enabled: bool = True,
            max_serialized_size: int = 10 * 1024 * 1024,  # 10 MB default
            validate_keys: bool = False,
            cleanup_interval: int = 900,  # 15 minutes default
    ):
        """
        Initialize the cache.

        Args:
            adapter: Cache adapter implementation
            default_ttl: Default time-to-live in seconds (None for no expiry)
            enabled: Whether caching is enabled by default
            max_serialized_size: Maximum size in bytes for serialized objects
            validate_keys: Whether to validate generated cache keys for format/uniqueness
            cleanup_interval: Interval in seconds for automatic cleanup of stale entry ID mappings
                             Set to 0 to disable automatic cleanup
        """
        self.adapter = adapter
        self.default_ttl = default_ttl
        self.enabled = enabled
        self.max_serialized_size = max_serialized_size
        self.validate_keys = validate_keys
        self.cleanup_interval = cleanup_interval
        # Mapping of entry_id to cache key for reverse lookups
        self.entry_key_map: Dict[str, str] = {}
        # Lock for thread-safe access to entry_key_map
        self._map_lock = asyncio.Lock()
        # Optional set of generated keys for validation
        self._generated_keys: Optional[Set[str]] = set() if validate_keys else None
        # Flag for string fallback in serialization
        self.fallback_to_str: bool = False
        # Cleanup task reference
        self._cleanup_task: Optional[asyncio.Task] = None
        # Flag to signal cleanup task to stop
        self._cleanup_running = False

    def _build_context(self, fn_name: str, args: tuple, kwargs: dict, entry_id: Optional[str] = None,
                       metadata: Optional[Dict[str, Any]] = None) -> CacheKeyContext:
        """
        Build a context dictionary for cache key generation.

        Args:
            fn_name: Name of the function
            args: Positional arguments
            kwargs: Keyword arguments
            entry_id: Optional entry ID
            metadata: Optional metadata dictionary

        Returns:
            Context dictionary for cache key generation
        """
        context = {
            "fn_name": fn_name,
            "args": args,
            "kwargs": kwargs,
        }

        if entry_id is not None:
            context["entry_id"] = entry_id

        if metadata is not None:
            context["metadata"] = metadata

        return context

    @staticmethod
    def default_key_fn(context: CacheKeyContext) -> str:
        """
        Default cache key generation function.
        Creates a deterministic hash from function name and arguments.

        Args:
            context: Context dictionary with entry information

        Returns:
            Cache key string
        """
        try:
            # Extract required elements from context
            fn_name = context["fn_name"]
            args = context["args"]
            kwargs = context["kwargs"]

            # Sort kwargs by key for consistent serialization
            sorted_kwargs = dict(sorted(kwargs.items()))

            # Create a data structure to serialize
            data_to_hash = [fn_name, args, sorted_kwargs]

            # Serialize with msgpack
            packed_data = MsgPackSerializer.encode(data_to_hash)

            # Generate MD5 hash of the serialized data
            return hashlib.md5(packed_data).hexdigest()
        except (KeyError, SerializationError) as e:
            # Extract fn_name safely, with fallback
            try:
                fn_name = context.get("fn_name", "unknown")
            except:
                fn_name = "unknown"

            logger.warning(f"Error generating cache key: {str(e)}")
            # Generate a unique key that won't match anything else
            return f"error_{fn_name}_{uuid.uuid4().hex}"

    def _validate_key(self, key: str) -> None:
        """
        Validate a cache key for format and uniqueness.
        
        Args:
            key: The cache key to validate
            
        Raises:
            InvalidCacheKeyError: If the key is invalid or already in use
        """
        if not self.validate_keys:
            return

        # Basic format validation
        if not key or not isinstance(key, str):
            raise InvalidCacheKeyError(f"Invalid cache key: {key}")

        # Check for uniqueness if tracking is enabled
        if self._generated_keys is not None:
            if key in self._generated_keys:
                logger.warning(f"Duplicate cache key detected: {key}")
            else:
                self._generated_keys.add(key)

    @contextlib.contextmanager
    def temporarily_disabled(self) -> Iterator[None]:
        """
        Context manager that temporarily disables the cache.
        
        Example:
        
        ```python
        # Temporarily disable the cache
        with cache.temporarily_disabled():
            # Cache operations in this block will be skipped
            await cache.set("func", args=(), kwargs={}, result="data")
            # All cache lookups will return misses
            hit, _ = await cache.get("func", args=(), kwargs={})  # hit will be False
        ```
        
        Yields:
            None
        """
        original_state = self.enabled
        try:
            self.enabled = False
            yield
        finally:
            self.enabled = original_state

    async def get(
            self,
            fn_name: str,
            args: tuple,
            kwargs: dict,
            cache_key_fn: Optional[CacheKeyFn] = None,
            entry_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Any]:
        """
        Get cached result.

        Args:
            fn_name: Name of the function
            args: Positional arguments
            kwargs: Keyword arguments
            cache_key_fn: Optional custom key generation function
            entry_id: Optional entry ID for reverse mapping
            metadata: Optional metadata for custom key generation

        Returns:
            Tuple of (cache_hit, result)
            
        Raises:
            InvalidCacheKeyError: If key validation is enabled and the key is invalid
        """
        if not self.enabled:
            return False, None

        try:
            # Build context for key generation
            context = self._build_context(fn_name, args, kwargs, entry_id, metadata)

            # Generate key using provided function or default
            key_fn = cache_key_fn or self.default_key_fn
            try:
                key = key_fn(context)
                # Validate key if enabled
                self._validate_key(key)
            except Exception as e:
                if isinstance(e, InvalidCacheKeyError):
                    raise
                logger.warning(f"Error in cache key function: {str(e)}")
                # Fall back to default key generation
                key = self.default_key_fn(context)

            # Store entry_id to key mapping if provided
            if entry_id is not None:
                async with self._map_lock:
                    self.entry_key_map[entry_id] = key

            # Get from cache
            return await self.adapter.get(key)
        except InvalidCacheKeyError as e:
            # Re-raise validation errors for better handling
            raise
        except Exception as e:
            logger.warning(f"Error retrieving from cache: {str(e)}")
            return False, None

    async def set(
            self,
            fn_name: str,
            args: tuple,
            kwargs: dict,
            result: Any,
            ttl: Optional[int] = None,
            cache_key_fn: Optional[CacheKeyFn] = None,
            entry_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Store result in cache.

        Args:
            fn_name: Name of the function
            args: Positional arguments
            kwargs: Keyword arguments
            result: Result to cache
            ttl: Time-to-live override (uses default if None)
            cache_key_fn: Optional custom key generation function
            entry_id: Optional entry ID for reverse mapping
            metadata: Optional metadata for custom key generation

        Returns:
            True if successfully cached, False otherwise
            
        Raises:
            InvalidCacheKeyError: If key validation is enabled and the key is invalid
        """
        if not self.enabled:
            return False

        try:
            # Try to serialize the result.
            # By default, do not use string fallback to ensure proper round-trip serialization
            try:
                serialized = MsgPackSerializer.encode(
                    result,
                    fallback=self.fallback_to_str
                )

                # If fallback is enabled, add a warning log to help diagnose potential issues
                if self.fallback_to_str:
                    logger.debug(f"Serializing result for {fn_name} with string fallback enabled")
            except SerializationError as e:
                logger.warning(f"Result for {fn_name} is not serializable: {str(e)}")
                return False

            # Check if the serialized result exceeds the maximum allowed size
            if len(serialized) > self.max_serialized_size:
                logger.warning(
                    f"Serialized result size {len(serialized)} exceeds maximum allowed size {self.max_serialized_size}"
                )
                return False

            # Build context for key generation
            context = self._build_context(fn_name, args, kwargs, entry_id, metadata)

            # Generate key using provided function or default
            key_fn = cache_key_fn or self.default_key_fn
            try:
                key = key_fn(context)
                # Validate key if enabled
                self._validate_key(key)
            except Exception as e:
                if isinstance(e, InvalidCacheKeyError):
                    raise
                logger.warning(f"Error in cache key function: {str(e)}")
                # Fall back to default key generation
                key = self.default_key_fn(context)

            # Store entry_id to key mapping if provided
            if entry_id is not None:
                async with self._map_lock:
                    self.entry_key_map[entry_id] = key

            effective_ttl = ttl if ttl is not None else self.default_ttl
            await self.adapter.set(key, result, effective_ttl)
            return True
        except InvalidCacheKeyError as e:
            # Re-raise validation errors for better handling
            raise
        except Exception as e:
            logger.warning(f"Error storing in cache: {str(e)}")
            return False

    async def invalidate(
            self,
            fn_name: str,
            args: tuple,
            kwargs: dict,
            cache_key_fn: Optional[CacheKeyFn] = None,
            entry_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Invalidate a specific cache entry.

        Args:
            fn_name: Name of the function
            args: Positional arguments
            kwargs: Keyword arguments
            cache_key_fn: Optional custom key generation function
            entry_id: Optional entry ID for lookup
            metadata: Optional metadata for custom key generation

        Returns:
            True if invalidated, False if not found
            
        Raises:
            InvalidCacheKeyError: If key validation is enabled and the key is invalid
        """
        try:
            # Build context for key generation
            context = self._build_context(fn_name, args, kwargs, entry_id, metadata)

            # Generate key using provided function or default
            key_fn = cache_key_fn or self.default_key_fn
            try:
                key = key_fn(context)
                # No need to validate on invalidation, just use the key
            except Exception as e:
                if isinstance(e, InvalidCacheKeyError):
                    raise
                logger.warning(f"Error in cache key function during invalidation: {str(e)}")
                # Fall back to default key generation
                key = self.default_key_fn(context)

            # Remove from entry_id mapping if it exists
            if entry_id is not None:
                async with self._map_lock:
                    if entry_id in self.entry_key_map:
                        del self.entry_key_map[entry_id]

            # Remove from generated keys if tracking is enabled
            if self._generated_keys is not None and key in self._generated_keys:
                self._generated_keys.remove(key)

            return await self.adapter.delete(key)
        except InvalidCacheKeyError as e:
            # Re-raise validation errors for better handling
            raise
        except Exception as e:
            logger.warning(f"Error invalidating cache: {str(e)}")
            return False

    async def invalidate_by_id(self, entry_id: str) -> bool:
        """
        Invalidate a cache entry using an entry ID.

        Args:
            entry_id: Entry ID associated with the cache entry

        Returns:
            True if invalidated, False if not found
        """
        # Check if entry ID exists with lock to avoid race condition
        async with self._map_lock:
            if entry_id not in self.entry_key_map:
                return False
            key = self.entry_key_map[entry_id]

        try:
            success = await self.adapter.delete(key)

            if success:
                async with self._map_lock:
                    if entry_id in self.entry_key_map:
                        del self.entry_key_map[entry_id]

                # Remove from generated keys if tracking is enabled
                if self._generated_keys is not None and key in self._generated_keys:
                    self._generated_keys.remove(key)

            return success
        except Exception as e:
            logger.warning(f"Error invalidating cache by entry ID: {str(e)}")
            return False

    async def get_cache_key_for_id(self, entry_id: str) -> Optional[str]:
        """
        Get the cache key associated with an entry ID.

        Args:
            entry_id: Entry ID to lookup

        Returns:
            Cache key or None if not found
        """
        async with self._map_lock:
            return self.entry_key_map.get(entry_id)

    async def get_id_key_mapping(self) -> Dict[str, str]:
        """
        Get a copy of the current entry ID to cache key mapping.
        
        Useful for diagnostics or high-performance operations.
        
        Returns:
            Dictionary mapping entry IDs to cache keys
        """
        async with self._map_lock:
            return self.entry_key_map.copy()

    async def clear(self) -> None:
        """Clear all cached results."""
        try:
            await self.adapter.clear()
            # Clear entry key map with lock
            async with self._map_lock:
                self.entry_key_map.clear()
            # Clear generated keys tracking if enabled
            if self._generated_keys is not None:
                self._generated_keys.clear()
        except Exception as e:
            logger.error(f"Error clearing cache: {str(e)}")

    async def start_cleanup_task(self) -> None:
        """
        Start the periodic cleanup task to remove stale entry ID mappings.
        
        This task runs in the background at the interval specified in the constructor
        and removes any entry_id -> key mappings where the key no longer exists in the cache,
        preventing memory leaks from expired or deleted cache entries.
        
        The task only runs if cleanup_interval > 0.
        """
        if self.cleanup_interval <= 0 or self._cleanup_task is not None:
            return

        self._cleanup_running = True
        self._cleanup_task = asyncio.create_task(self._run_cleanup_task())

    async def stop_cleanup_task(self) -> None:
        """
        Stop the periodic cleanup task.
        
        This method should be called when shutting down the cache to properly
        clean up the background task.
        """
        self._cleanup_running = False

        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            finally:
                self._cleanup_task = None

    async def _run_cleanup_task(self) -> None:
        """
        Background task that periodically cleans up stale entry ID mappings.
        Automatically restarts after errors with a backoff delay.
        """
        backoff_delay = 5  # Start with 5 seconds before retry
        max_backoff = 300  # Maximum backoff of 5 minutes
        
        while self._cleanup_running:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_stale_mappings()
                # Reset backoff on successful execution
                backoff_delay = 5
            except asyncio.CancelledError:
                # Task was cancelled, exit gracefully
                break
            except Exception as e:
                # Log the error but don't crash
                logger.error(f"Error in cache cleanup task: {str(e)}")
                
                if self._cleanup_running:
                    # Wait before retrying with exponential backoff
                    logger.info(f"Cleanup task will retry in {backoff_delay} seconds")
                    await asyncio.sleep(backoff_delay)
                    # Increase backoff for next failure, capped at max_backoff
                    backoff_delay = min(backoff_delay * 2, max_backoff)

    async def _cleanup_stale_mappings(self) -> None:
        """
        Remove any entry_id -> key mappings where the key no longer exists in the cache.
        
        This prevents memory leaks from expired cache entries.
        """
        # Check if map is empty (with lock to avoid race condition)
        async with self._map_lock:
            if not self.entry_key_map:
                return
            # Make a copy to avoid modification during iteration
            entry_ids_to_check = list(self.entry_key_map.keys())

        try:
            stale_count = 0
            stale_entry_ids = []
            stale_keys = []

            # First, check all keys without holding the lock for too long
            for entry_id in entry_ids_to_check:
                # Get key (with lock) - entry_id may be gone by now due to concurrent operations
                key = None
                async with self._map_lock:
                    if entry_id in self.entry_key_map:
                        key = self.entry_key_map[entry_id]
                    else:
                        continue

                # Check if key exists in cache - outside lock to avoid holding it during I/O
                if key:
                    hit, _ = await self.adapter.get(key)
                    if not hit:
                        # Found a stale mapping - save for later batch removal
                        stale_entry_ids.append(entry_id)
                        stale_keys.append(key)

            # Remove stale mappings in one batch operation with the lock
            if stale_entry_ids:
                async with self._map_lock:
                    for entry_id in stale_entry_ids:
                        if entry_id in self.entry_key_map:
                            del self.entry_key_map[entry_id]
                            stale_count += 1

                # Remove from generated keys if tracking (no lock needed)
                if self._generated_keys is not None:
                    for key in stale_keys:
                        if key in self._generated_keys:
                            self._generated_keys.remove(key)

            if stale_count > 0:
                logger.debug(f"Cleaned up {stale_count} stale entry ID mappings")

        except Exception as e:
            logger.warning(f"Error during entry mapping cleanup: {str(e)}")
