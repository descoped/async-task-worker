"""
Example of using async_cache standalone and with AsyncTaskWorker.

This example demonstrates:
1. Basic cache usage
2. Custom key generation
3. Integration with AsyncTaskWorker
"""

import asyncio
import datetime
import logging
import uuid
from typing import Dict, Any

from async_cache import AsyncCache, compose_key_functions, extract_key_component, key_component
from async_cache.adapters import MemoryCacheAdapter
from async_task_worker import AsyncTaskWorker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ----- STANDALONE CACHE EXAMPLE -----

async def standalone_cache_example():
    """Demonstrate standalone async_cache usage."""
    logger.info("===== STANDALONE CACHE EXAMPLE =====")

    # Create memory cache adapter with 100 item limit
    adapter = MemoryCacheAdapter(max_size=100)

    # Create cache with 5 second default TTL
    cache = AsyncCache(
        adapter=adapter,
        default_ttl=5,
        enabled=True,
        cleanup_interval=60
    )

    # Start background cleanup task
    await cache.start_cleanup_task()

    try:
        # Basic cache operations
        logger.info("Basic cache operations:")

        # Cache a simple value
        await cache.set("get_data", args=(), kwargs={"id": 123}, result={"name": "Test", "value": 42})

        # Retrieve it
        hit, result = await cache.get("get_data", args=(), kwargs={"id": 123})
        logger.info(f"Cache hit: {hit}, result: {result}")

        # Invalidate it
        await cache.invalidate("get_data", args=(), kwargs={"id": 123})
        hit, result = await cache.get("get_data", args=(), kwargs={"id": 123})
        logger.info(f"After invalidation - hit: {hit}")

        # Custom key generation
        logger.info("\nCustom key generation:")

        # Define a named key component for user ID
        @key_component("user")
        def user_key(context):
            return str(context["kwargs"].get("user_id", "guest"))

        # Define a named key component for API version
        @key_component("version")
        def api_version(context):
            return context.get("metadata", {}).get("version", "1.0")

        # Compose them together
        composite_key = compose_key_functions(user_key, api_version)

        # Store with custom key function and metadata
        logger.info("Storing with custom key function (user:123:version:2.0)")
        await cache.set(
            "get_user_profile",
            args=(),
            kwargs={"user_id": 123},
            result={"name": "Alice", "role": "admin"},
            metadata={"version": "2.0"},
            cache_key_fn=composite_key
        )

        # Retrieve with same keys
        hit, result = await cache.get(
            "get_user_profile",
            args=(),
            kwargs={"user_id": 123},
            metadata={"version": "2.0"},
            cache_key_fn=composite_key
        )
        logger.info(f"Same keys - hit: {hit}, result: {result}")

        # Try different version (should miss)
        hit, result = await cache.get(
            "get_user_profile",
            args=(),
            kwargs={"user_id": 123},
            metadata={"version": "3.0"},
            cache_key_fn=composite_key
        )
        logger.info(f"Different version - hit: {hit}")

        # Using entry IDs for direct invalidation
        logger.info("\nUsing entry IDs:")

        entry_id = f"entry-{uuid.uuid4()}"
        logger.info(f"Created entry ID: {entry_id}")

        await cache.set(
            "get_product",
            args=(),
            kwargs={"product_id": 456},
            result={"name": "Widget", "price": 19.99},
            entry_id=entry_id
        )

        # Get cache key for entry ID
        key = await cache.get_cache_key_for_id(entry_id)
        logger.info(f"Cache key for entry ID: {key}")

        # Invalidate by entry ID
        await cache.invalidate_by_id(entry_id)
        logger.info("Invalidated by entry ID")

        # Verify it's gone
        hit, _ = await cache.get("get_product", args=(), kwargs={"product_id": 456})
        logger.info(f"After invalidation by ID - hit: {hit}")

    finally:
        # Clean up
        await cache.stop_cleanup_task()
        await cache.clear()


# ----- ASYNC TASK WORKER INTEGRATION -----

# Sample data store
PRODUCTS = {
    1: {"name": "Widget", "price": 9.99, "stock": 42},
    2: {"name": "Gadget", "price": 19.99, "stock": 15},
    3: {"name": "Doohickey", "price": 29.99, "stock": 8},
}


class CachedTaskWorker:
    """Example of AsyncTaskWorker with custom cache integration."""

    def __init__(self):
        # Create cache
        self.cache_adapter = MemoryCacheAdapter(max_size=100)
        self.cache = AsyncCache(
            adapter=self.cache_adapter,
            default_ttl=30,  # 30 second TTL
            enabled=True
        )

        # Create task worker with registered tasks
        self.worker = AsyncTaskWorker(max_workers=5)
        self.worker.register_task("get_product", self.get_product)
        self.worker.register_task("update_product", self.update_product)

        # Create custom key functions
        self.product_key = extract_key_component("kwargs.product_id")
        self.version_key = extract_key_component("metadata.version")
        self.composite_key = compose_key_functions(self.product_key, self.version_key)

    async def start(self):
        """Start the worker and cache cleanup task."""
        await self.worker.start()
        await self.cache.start_cleanup_task()

    async def stop(self):
        """Stop the worker and cache cleanup task."""
        await self.worker.stop()
        await self.cache.stop_cleanup_task()
        await self.cache.clear()

    async def get_product(self, product_id: int) -> Dict[str, Any]:
        """
        Get product details, using cache when available.
        
        This demonstrates a task using the cache directly.
        """
        # Check cache first
        hit, cached_result = await self.cache.get(
            "get_product",
            args=(),
            kwargs={"product_id": product_id},
            metadata={"version": "1.0"},
            cache_key_fn=self.composite_key
        )

        if hit:
            logger.info(f"Cache hit for product_id={product_id}")
            return cached_result

        # Cache miss, do the "expensive" operation
        logger.info(f"Cache miss for product_id={product_id}, fetching from database")

        # Simulate database delay
        await asyncio.sleep(0.5)

        if product_id not in PRODUCTS:
            raise ValueError(f"Product {product_id} not found")

        result = {
            **PRODUCTS[product_id],
            "fetched_at": datetime.datetime.now().isoformat(),
        }

        # Store in cache
        await self.cache.set(
            "get_product",
            args=(),
            kwargs={"product_id": product_id},
            result=result,
            metadata={"version": "1.0"},
            cache_key_fn=self.composite_key
        )

        return result

    async def update_product(self, product_id: int, **updates) -> Dict[str, Any]:
        """
        Update a product and invalidate its cache entry.
        
        This demonstrates cache invalidation when data changes.
        """
        if product_id not in PRODUCTS:
            raise ValueError(f"Product {product_id} not found")

        # Update the product
        for key, value in updates.items():
            if key in PRODUCTS[product_id]:
                PRODUCTS[product_id][key] = value

        # Invalidate cache for this product
        await self.cache.invalidate(
            "get_product",
            args=(),
            kwargs={"product_id": product_id},
            metadata={"version": "1.0"},
            cache_key_fn=self.composite_key
        )

        logger.info(f"Invalidated cache for product_id={product_id}")

        return {
            **PRODUCTS[product_id],
            "updated_at": datetime.datetime.now().isoformat(),
        }


async def task_worker_example():
    """Demonstrate AsyncTaskWorker with cache integration."""
    logger.info("\n===== ASYNC TASK WORKER INTEGRATION =====")

    worker = CachedTaskWorker()
    await worker.start()

    try:
        # Submit task to get product (should cache miss first time)
        logger.info("Getting product 1 (first request, expect cache miss)")
        task = await worker.worker.submit_task("get_product", product_id=1)
        result = await task.wait()
        logger.info(f"Result: {result}")

        # Get again (should hit cache)
        logger.info("\nGetting product 1 again (expect cache hit)")
        task = await worker.worker.submit_task("get_product", product_id=1)
        result = await task.wait()
        logger.info(f"Result: {result}")

        # Update product (should invalidate cache)
        logger.info("\nUpdating product 1")
        task = await worker.worker.submit_task("update_product", product_id=1, price=12.99)
        result = await task.wait()
        logger.info(f"Update result: {result}")

        # Get again (should cache miss due to invalidation)
        logger.info("\nGetting product 1 after update (expect cache miss)")
        task = await worker.worker.submit_task("get_product", product_id=1)
        result = await task.wait()
        logger.info(f"Result with new price: {result}")

        # Get again (should hit cache)
        logger.info("\nGetting product 1 final time (expect cache hit)")
        task = await worker.worker.submit_task("get_product", product_id=1)
        result = await task.wait()
        logger.info(f"Final result: {result}")

    finally:
        await worker.stop()


async def main():
    """Run all examples."""
    await standalone_cache_example()
    await task_worker_example()


if __name__ == "__main__":
    asyncio.run(main())
