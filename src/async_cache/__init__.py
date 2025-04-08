"""Async Cache - A flexible asynchronous caching system."""

# Core components
from async_cache.core import AsyncCache, CacheAdapter

# Key utilities
from async_cache.key_utils import (
    CacheKeyContext,
    CacheKeyFn,
    CacheKeyComponent,
    compose_key_functions,
    extract_key_component,
    key_component,
)

# Serialization
from async_cache.serialization import MsgPackSerializer

# Exceptions
from async_cache.exceptions import (
    CacheError,
    SerializationError,
    InvalidCacheKeyError,
)

# Adapters
from async_cache.adapters import MemoryCacheAdapter

# Try to import Redis adapter if available
try:
    from async_cache.adapters import RedisCacheAdapter
    __all__ = [
        # Core
        "AsyncCache",
        "CacheAdapter",
        
        # Key utils
        "CacheKeyContext",
        "CacheKeyFn",
        "CacheKeyComponent",
        "compose_key_functions",
        "extract_key_component",
        "key_component",
        
        # Serialization
        "MsgPackSerializer",
        
        # Exceptions
        "CacheError",
        "SerializationError",
        "InvalidCacheKeyError",
        
        # Adapters
        "MemoryCacheAdapter",
        "RedisCacheAdapter",
    ]
except ImportError:
    # Redis is not available
    __all__ = [
        # Core
        "AsyncCache",
        "CacheAdapter",
        
        # Key utils
        "CacheKeyContext",
        "CacheKeyFn",
        "CacheKeyComponent",
        "compose_key_functions",
        "extract_key_component",
        "key_component",
        
        # Serialization
        "MsgPackSerializer",
        
        # Exceptions
        "CacheError",
        "SerializationError",
        "InvalidCacheKeyError",
        
        # Adapters
        "MemoryCacheAdapter",
    ]

