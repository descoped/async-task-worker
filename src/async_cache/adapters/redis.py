"""
Redis cache adapter for the async_cache package.

Provides a caching implementation that uses Redis as the backend.
Requires the 'redis' package to be installed (available with 'async-cache[redis]').
"""

import logging
from typing import Any, Optional, Tuple

from async_cache.core import CacheAdapter
from async_cache.serialization import MsgPackSerializer

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)


class RedisCacheAdapter(CacheAdapter):
    """
    Redis-based cache adapter.
    
    Uses Redis for distributed caching with TTL support.
    
    Note:
        Requires the 'redis' package: pip install async-cache[redis]
    """

    def __init__(
            self,
            host: str = "localhost",
            port: int = 6379,
            db: int = 0,
            password: Optional[str] = None,
            key_prefix: str = "async_cache:",
            connection_pool: Optional[Any] = None,
            client: Optional[Any] = None,
            **redis_kwargs
    ):
        """
        Initialize Redis cache adapter.
        
        Args:
            host: Redis host address
            port: Redis port number
            db: Redis database number
            password: Optional Redis password
            key_prefix: Prefix for all cache keys
            connection_pool: Optional existing Redis connection pool
            client: Optional existing Redis client instance
            **redis_kwargs: Additional arguments to pass to Redis client
            
        Raises:
            ImportError: If redis package is not installed
        """
        if redis is None:
            raise ImportError(
                "Redis adapter requires the 'redis' package. "
                "Install with 'pip install async-cache[redis]'"
            )

        self.key_prefix = key_prefix

        # Use provided client, or create a new one
        if client:
            self.redis = client
        else:
            if connection_pool:
                self.redis = redis.Redis(connection_pool=connection_pool, **redis_kwargs)
            else:
                self.redis = redis.Redis(
                    host=host,
                    port=port,
                    db=db,
                    password=password,
                    **redis_kwargs
                )

    def _prefixed_key(self, key: str) -> str:
        """Add the prefix to a cache key."""
        return f"{self.key_prefix}{key}"

    async def get(self, key: str) -> Tuple[bool, Any]:
        """
        Get a value from the Redis cache.
        
        Args:
            key: The cache key
            
        Returns:
            Tuple of (hit, value) where hit is True if the item was in cache
        """
        try:
            # Get from Redis
            prefixed_key = self._prefixed_key(key)
            data = await self.redis.get(prefixed_key)

            if data is None:
                return False, None

            # Deserialize
            value = MsgPackSerializer.decode(data)
            return True, value
        except Exception as e:
            logger.warning(f"Error getting from Redis cache: {str(e)}")
            return False, None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set a value in the Redis cache.
        
        Args:
            key: The cache key
            value: The value to store
            ttl: Optional time-to-live in seconds
        """
        try:
            # Serialize
            data = MsgPackSerializer.encode(value)

            # Store in Redis with TTL
            prefixed_key = self._prefixed_key(key)
            if ttl is not None:
                await self.redis.setex(prefixed_key, ttl, data)
            else:
                await self.redis.set(prefixed_key, data)
        except Exception as e:
            logger.warning(f"Error setting in Redis cache: {str(e)}")

    async def delete(self, key: str) -> bool:
        """
        Delete a value from the Redis cache.
        
        Args:
            key: The cache key
            
        Returns:
            True if deleted, False if not found
        """
        try:
            prefixed_key = self._prefixed_key(key)
            result = await self.redis.delete(prefixed_key)
            return result > 0
        except Exception as e:
            logger.warning(f"Error deleting from Redis cache: {str(e)}")
            return False

    async def clear(self) -> None:
        """
        Clear all items from the cache with the configured prefix.
        
        Note: This only clears keys with the specified prefix for safety.
        """
        try:
            # Use scan_iter to find all keys with prefix
            pattern = f"{self.key_prefix}*"

            # Get all keys matching pattern
            keys = [key async for key in self.redis.scan_iter(match=pattern)]

            if keys:
                # Delete all matching keys
                await self.redis.delete(*keys)
                logger.info(f"Cleared {len(keys)} keys from Redis cache")
            else:
                logger.debug("No keys found to clear from Redis cache")
        except Exception as e:
            logger.warning(f"Error clearing Redis cache: {str(e)}")

    async def close(self) -> None:
        """
        Close the Redis connection.
        
        This should be called when the adapter is no longer needed.
        """
        try:
            await self.redis.close()
        except Exception as e:
            logger.warning(f"Error closing Redis connection: {str(e)}")
