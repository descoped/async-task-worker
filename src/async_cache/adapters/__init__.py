"""Cache adapters for different storage backends."""

from async_cache.adapters.memory import MemoryCacheAdapter

# Try to import Redis adapter if redis is available
try:
    from async_cache.adapters.redis import RedisCacheAdapter
    __all__ = ["MemoryCacheAdapter", "RedisCacheAdapter"]
except ImportError:
    # Redis package is not installed
    __all__ = ["MemoryCacheAdapter"]
