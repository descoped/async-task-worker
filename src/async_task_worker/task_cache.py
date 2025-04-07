"""
Task Cache System

This module provides caching functionality for the AsyncTaskWorker
with improved serialization support.
"""

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class SerializationError(Exception):
    """Exception raised when serialization fails."""
    pass


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


import datetime
import msgpack
import types
import uuid
from typing import Any


class MsgPackSerializer:
    """Handler for msgpack serialization and deserialization with custom type support."""

    # Type codes for custom types
    TYPE_DATETIME = 1
    TYPE_CUSTOM_OBJECT = 2
    TYPE_UUID = 3
    TYPE_STRING_FALLBACK = 99  # Special code for string fallback representations

    @staticmethod
    def encode(obj: Any, fallback: bool = False) -> bytes:
        """
        Serialize an object to bytes using msgpack.

        Args:
            obj: The object to serialize
            fallback: Whether to fallback to string representation for unsupported types
                      IMPORTANT: If True, may not roundtrip correctly

        Returns:
            Serialized bytes

        Raises:
            SerializationError: If serialization fails and fallback is False
        """
        try:
            return msgpack.packb(
                obj,
                default=lambda o: MsgPackSerializer._encode_hook(o, fallback),
                use_bin_type=True
            )
        except Exception as e:
            raise SerializationError(f"Failed to serialize object: {str(e)}")

    @staticmethod
    def decode(data: bytes) -> Any:
        """
        Deserialize bytes back to an object.

        Args:
            data: The serialized data

        Returns:
            Deserialized object

        Raises:
            SerializationError: If deserialization fails
        """
        try:
            return msgpack.unpackb(data, object_hook=MsgPackSerializer._decode_hook, raw=False)
        except Exception as e:
            raise SerializationError(f"Failed to deserialize data: {str(e)}")

    @staticmethod
    def _encode_hook(obj: Any, fallback: bool = False) -> Any:
        """
        Custom encoder for handling Python types not natively supported by msgpack.
        
        Note: When fallback is True, objects that can't be properly serialized
        will be converted to a special string representation format that includes
        the original type. These won't roundtrip to their original type.
        """
        # Handle datetime objects - fully supported for roundtrip
        if isinstance(obj, datetime.datetime):
            return {
                "__type_code": MsgPackSerializer.TYPE_DATETIME,
                "data": obj.isoformat()
            }

        # Handle UUID objects - fully supported for roundtrip
        if isinstance(obj, uuid.UUID):
            return {
                "__type_code": MsgPackSerializer.TYPE_UUID,
                "data": str(obj)
            }

        # Explicitly handle functions (including lambdas)
        if isinstance(obj, types.FunctionType):
            if fallback:
                return {
                    "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                    "type": type(obj).__name__,
                    "data": str(obj)
                }
            else:
                raise SerializationError(f"Functions of type {type(obj).__name__} are not serializable")

        # Handle objects with __dict__
        if hasattr(obj, "__dict__"):
            try:
                # Ensure the __dict__ is serializable
                serializable_dict = {}
                for key, value in obj.__dict__.items():
                    try:
                        # Test each value for serializability
                        msgpack.packb(
                            value,
                            default=lambda o: MsgPackSerializer._encode_hook(o, fallback=False),
                            use_bin_type=True
                        )
                        serializable_dict[key] = value
                    except Exception:
                        if fallback:
                            # If value can't be serialized, use string representation with type info
                            serializable_dict[key] = {
                                "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                                "type": type(value).__name__,
                                "data": str(value)
                            }
                        else:
                            # Without fallback, propagate the exception
                            raise

                return {
                    "__type_code": MsgPackSerializer.TYPE_CUSTOM_OBJECT,
                    "type": obj.__class__.__name__,
                    "module": obj.__class__.__module__,
                    "data": serializable_dict
                }
            except Exception as e:
                if fallback:
                    return {
                        "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                        "type": type(obj).__name__,
                        "data": str(obj)
                    }
                raise SerializationError(f"Object of type {type(obj).__name__} is not serializable: {str(e)}")

        # Handle objects with __slots__
        if hasattr(obj, "__slots__"):
            try:
                slot_dict = {}
                for slot in obj.__slots__:
                    if hasattr(obj, slot):
                        value = getattr(obj, slot)
                        try:
                            # Test each value for serializability
                            msgpack.packb(
                                value,
                                default=lambda o: MsgPackSerializer._encode_hook(o, fallback=False),
                                use_bin_type=True
                            )
                            slot_dict[slot] = value
                        except Exception:
                            if fallback:
                                # If value can't be serialized, use string representation with type info
                                slot_dict[slot] = {
                                    "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                                    "type": type(value).__name__,
                                    "data": str(value)
                                }
                            else:
                                # Without fallback, propagate the exception
                                raise

                return {
                    "__type_code": MsgPackSerializer.TYPE_CUSTOM_OBJECT,
                    "type": obj.__class__.__name__,
                    "module": obj.__class__.__module__,
                    "data": slot_dict
                }
            except Exception as e:
                if fallback:
                    return {
                        "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                        "type": type(obj).__name__,
                        "data": str(obj)
                    }
                raise SerializationError(f"Object of type {type(obj).__name__} is not serializable: {str(e)}")

        # As a last resort, if fallback is enabled, convert to string but preserve type info
        if fallback:
            return {
                "__type_code": MsgPackSerializer.TYPE_STRING_FALLBACK,
                "type": type(obj).__name__,
                "data": str(obj)
            }

        # If we reach here without returning and fallback is False, raise an error
        raise SerializationError(f"Object of type {type(obj).__name__} is not serializable")

    @staticmethod
    def _decode_hook(obj: Dict[str, Any]) -> Any:
        """Custom decoder for handling Python types not natively supported by msgpack."""
        # Only process dictionaries with a type code
        if not isinstance(obj, dict) or "__type_code" not in obj:
            return obj

        type_code = obj["__type_code"]

        # Handle datetime objects
        if type_code == MsgPackSerializer.TYPE_DATETIME:
            return datetime.datetime.fromisoformat(obj["data"])

        # Handle UUID objects
        if type_code == MsgPackSerializer.TYPE_UUID:
            return uuid.UUID(obj["data"])

        # Handle string fallback representations - return with clear type annotation
        if type_code == MsgPackSerializer.TYPE_STRING_FALLBACK:
            return {
                "__serialized_string_of_type": obj["type"],
                "value": obj["data"]
            }

        # Handle custom objects
        if type_code == MsgPackSerializer.TYPE_CUSTOM_OBJECT:
            return {
                "__type": obj["type"],
                "__module": obj["module"],
                **obj["data"]
            }

        return obj


class TaskCache:
    """
    Task cache manager for AsyncTaskWorker.
    Uses improved serialization with better type support.
    """

    def __init__(
            self,
            adapter: CacheAdapter,
            default_ttl: Optional[int] = None,
            enabled: bool = True,
            max_serialized_size: int = 10 * 1024 * 1024,  # 10 MB default
    ):
        """
        Initialize the task cache.

        Args:
            adapter: Cache adapter implementation
            default_ttl: Default time-to-live in seconds (None for no expiry)
            enabled: Whether caching is enabled by default
            max_serialized_size: Maximum size in bytes for serialized objects
        """
        self.adapter = adapter
        self.default_ttl = default_ttl
        self.enabled = enabled
        self.max_serialized_size = max_serialized_size

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
        try:
            # Sort kwargs by key for consistent serialization
            sorted_kwargs = dict(sorted(kwargs.items()))

            # Create a data structure to serialize
            data_to_hash = [func_name, args, sorted_kwargs]

            # Serialize with msgpack
            packed_data = MsgPackSerializer.encode(data_to_hash)

            # Generate MD5 hash of the serialized data
            return hashlib.md5(packed_data).hexdigest()
        except SerializationError as e:
            logger.warning(f"Error generating cache key: {str(e)}")
            # Generate a unique key that won't match anything else
            return f"error_{func_name}_{uuid.uuid4().hex}"

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

        try:
            key = self.generate_key(func_name, args, kwargs)
            return await self.adapter.get(key)
        except Exception as e:
            logger.warning(f"Error retrieving from cache: {str(e)}")
            return False, None

    async def set(
            self,
            func_name: str,
            args: tuple,
            kwargs: dict,
            result: Any,
            ttl: Optional[int] = None
    ) -> bool:
        """
        Store result in cache.

        Args:
            func_name: Name of the task function
            args: Positional arguments
            kwargs: Keyword arguments
            result: Result to cache
            ttl: Time-to-live override (uses default if None)

        Returns:
            True if successfully cached, False otherwise
        """
        if not self.enabled:
            return False

        try:
            # Try to serialize the result. 
            # By default, do not use string fallback to ensure proper round-trip serialization
            # Clients can explicitly enable fallback via the TaskCache.fallback_to_str attribute
            fallback_enabled = getattr(self, "fallback_to_str", False)

            try:
                serialized = MsgPackSerializer.encode(
                    result,
                    fallback=fallback_enabled
                )

                # If fallback is enabled, add a warning log to help diagnose potential issues
                if fallback_enabled:
                    logger.debug(f"Serializing task result for {func_name} with string fallback enabled")
            except SerializationError as e:
                logger.warning(f"Task result for {func_name} is not serializable: {str(e)}")
                return False

            # Check if the serialized result exceeds the maximum allowed size
            if len(serialized) > self.max_serialized_size:
                logger.warning(
                    f"Serialized result size {len(serialized)} exceeds maximum allowed size {self.max_serialized_size}"
                )
                return False

            key = self.generate_key(func_name, args, kwargs)
            effective_ttl = ttl if ttl is not None else self.default_ttl
            await self.adapter.set(key, result, effective_ttl)
            return True
        except Exception as e:
            logger.warning(f"Error storing in cache: {str(e)}")
            return False

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
        try:
            key = self.generate_key(func_name, args, kwargs)
            return await self.adapter.delete(key)
        except Exception as e:
            logger.warning(f"Error invalidating cache: {str(e)}")
            return False

    async def clear(self) -> None:
        """Clear all cached results."""
        try:
            await self.adapter.clear()
        except Exception as e:
            logger.error(f"Error clearing cache: {str(e)}")
