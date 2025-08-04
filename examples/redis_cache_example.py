"""
Example using the Redis adapter for async_cache.

This example demonstrates:
1. Setting up the Redis adapter
2. Basic cache operations with Redis
3. Handling connection issues gracefully

To run this example, you need Redis installed and running locally,
or modify the connection parameters to point to your Redis server.

Install requirements: pip install async-cache[redis]
"""

import asyncio
import logging
import os
import time
import uuid

from async_cache import AsyncCache
from async_cache.adapters.memory import MemoryCacheAdapter

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Try to import Redis adapter
try:
    from async_cache.adapters.redis import RedisCacheAdapter

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis adapter not available. Install with 'pip install async-cache[redis]'")


async def run_redis_example():
    """Run the Redis cache example if available."""
    if not REDIS_AVAILABLE:
        logger.error("Cannot run Redis example: Redis adapter not available")
        return

    logger.info("===== REDIS CACHE EXAMPLE =====")

    # Redis connection parameters - modify as needed
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    redis_db = int(os.environ.get("REDIS_DB", "0"))
    redis_password = os.environ.get("REDIS_PASSWORD", None)

    # Create the Redis adapter with unique prefix for this run
    run_id = uuid.uuid4().hex[:8]
    adapter = None
    cache = None

    try:
        # Create Redis adapter
        adapter = RedisCacheAdapter(
            host=redis_host, port=redis_port, db=redis_db, password=redis_password, key_prefix=f"example:{run_id}:"
        )

        # Create cache with Redis adapter
        cache = AsyncCache(
            adapter=adapter,
            default_ttl=30,  # 30 seconds TTL
            enabled=True,
        )

        # Start the cleanup task
        await cache.start_cleanup_task()

        # Basic cache operations
        logger.info("Testing basic cache operations with Redis...")

        # Cache a value
        user_data = {"id": 42, "name": "Redis User", "email": "user@example.com", "created_at": time.time()}

        await cache.set("get_user", args=(), kwargs={"user_id": 42}, result=user_data)
        logger.info("Stored user data in Redis")

        # Retrieve the value
        hit, result = await cache.get("get_user", args=(), kwargs={"user_id": 42})

        if hit:
            logger.info(f"Cache hit! Retrieved user: {result}")
        else:
            logger.warning("Cache miss - something went wrong")

        # Test TTL expiration
        logger.info("\nTesting TTL expiration (5 second TTL)...")

        # Store with short TTL
        await cache.set(
            "short_ttl_data",
            args=(),
            kwargs={},
            result={"timestamp": time.time()},
            ttl=5,  # 5 second TTL
        )

        # Verify it's there
        hit, _ = await cache.get("short_ttl_data", args=(), kwargs={})
        logger.info(f"Immediate retrieval - cache hit: {hit}")

        # Wait for expiration
        logger.info("Waiting for TTL expiration (6 seconds)...")
        await asyncio.sleep(6)

        # Try to get expired data
        hit, _ = await cache.get("short_ttl_data", args=(), kwargs={})
        logger.info(f"After TTL - cache hit: {hit}")

        # Invalidate by key
        logger.info("\nTesting cache invalidation...")

        # Store with entry_id
        entry_id = "test-invalidate"
        await cache.set("invalidate_test", args=(), kwargs={}, result="This should be invalidated", entry_id=entry_id)

        # Verify it's there
        hit, _ = await cache.get("invalidate_test", args=(), kwargs={})
        logger.info(f"Before invalidation - cache hit: {hit}")

        # Invalidate by entry_id
        success = await cache.invalidate_by_id(entry_id)
        logger.info(f"Invalidation result: {success}")

        # Verify it's gone
        hit, _ = await cache.get("invalidate_test", args=(), kwargs={})
        logger.info(f"After invalidation - cache hit: {hit}")

        # Clear all data for this run
        logger.info("\nClearing cache...")
        await cache.clear()
        logger.info("Cache cleared")

    except Exception as e:
        logger.error(f"Redis example failed: {str(e)}")
    finally:
        # Ensure proper cleanup
        if cache:
            await cache.stop_cleanup_task()

        # Close Redis connection if adapter was created
        if adapter:
            try:
                await adapter.close()
            except Exception as e:
                logger.error(f"Error closing Redis connection: {str(e)}")


async def run_fallback_example():
    """Run example with fallback to memory cache."""
    logger.info("\n===== MEMORY FALLBACK EXAMPLE =====")

    # Create memory adapter as fallback
    adapter = MemoryCacheAdapter(max_size=100)

    # Create cache with memory adapter
    cache = AsyncCache(adapter=adapter, default_ttl=30, enabled=True)

    try:
        # Start the cleanup task
        await cache.start_cleanup_task()

        logger.info("Using in-memory cache as fallback")

        # Cache a value
        await cache.set("fallback_test", args=(), kwargs={}, result={"status": "using memory cache"})

        # Retrieve the value
        hit, result = await cache.get("fallback_test", args=(), kwargs={})
        logger.info(f"Cache hit: {hit}, result: {result}")

    finally:
        # Ensure proper cleanup
        await cache.stop_cleanup_task()
        await cache.clear()


async def main():
    """Run all examples."""
    # Try Redis example
    await run_redis_example()

    # Always run memory fallback
    await run_fallback_example()

    logger.info("\nAll examples completed")


if __name__ == "__main__":
    asyncio.run(main())
