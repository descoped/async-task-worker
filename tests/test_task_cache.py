"""
Unit tests for the task caching functionality.
"""

import asyncio
import time

import pytest

from async_task_worker import AsyncTaskWorker, TaskStatus, task
from async_task_worker.task_cache import MemoryCacheAdapter

# Counter for tracking function calls
call_counter = 0


@task("cached_task")
async def cached_task(value: int, progress_callback=None):
    """Task that can be cached"""
    global call_counter
    call_counter += 1

    # Report progress
    if progress_callback:
        progress_callback(0.5)
        await asyncio.sleep(0.1)
        progress_callback(1.0)

    # Return a result based on input
    return {"result": value * 2, "timestamp": time.time()}


@pytest.fixture
async def cache_worker():
    """Create a worker with caching enabled"""
    worker = AsyncTaskWorker(
        max_workers=2,
        cache_enabled=True,
        cache_ttl=60,  # 60 second TTL
        cache_max_size=100
    )
    await worker.start()
    yield worker
    await worker.stop()


@pytest.mark.asyncio
async def test_basic_caching(cache_worker):
    """Test that results are cached and retrieved"""
    global call_counter
    call_counter = 0

    # First call should execute the task
    task_id1 = await cache_worker.add_task(cached_task, 5)

    # Wait for completion
    await asyncio.sleep(0.5)

    # Check result
    info1 = cache_worker.get_task_info(task_id1)
    assert info1.status == TaskStatus.COMPLETED
    assert info1.result["result"] == 10
    assert call_counter == 1

    # Second call with same args should use cache
    task_id2 = await cache_worker.add_task(cached_task, 5)

    # Wait for completion
    await asyncio.sleep(0.2)

    # Check result and verify function wasn't called again
    info2 = cache_worker.get_task_info(task_id2)
    assert info2.status == TaskStatus.COMPLETED
    assert info2.result["result"] == 10
    assert call_counter == 1  # Still 1, no additional call

    # Call with different args should execute the task
    task_id3 = await cache_worker.add_task(cached_task, 10)

    # Wait for completion
    await asyncio.sleep(0.2)

    # Check result
    info3 = cache_worker.get_task_info(task_id3)
    assert info3.status == TaskStatus.COMPLETED
    assert info3.result["result"] == 20
    assert call_counter == 2  # New call


@pytest.mark.asyncio
async def test_cache_bypass(cache_worker):
    """Test bypassing the cache"""
    global call_counter
    call_counter = 0

    # First call
    task_id1 = await cache_worker.add_task(cached_task, 7)
    await asyncio.sleep(0.5)
    info1 = cache_worker.get_task_info(task_id1)
    assert call_counter == 1
    result1 = info1.result

    # Second call with cache bypass
    task_id2 = await cache_worker.add_task(cached_task, 7, use_cache=False)
    await asyncio.sleep(0.5)
    info2 = cache_worker.get_task_info(task_id2)
    assert call_counter == 2  # Should increment
    result2 = info2.result

    # Results should be different (different timestamps)
    assert result1["result"] == result2["result"]  # Same calculation
    assert result1["timestamp"] != result2["timestamp"]  # Different execution time


@pytest.mark.asyncio
async def test_cache_invalidation(cache_worker):
    """Test invalidating cache entries"""
    global call_counter
    call_counter = 0

    # Make a call to cache the result
    task_id1 = await cache_worker.add_task(cached_task, 12)
    await asyncio.sleep(0.5)
    assert call_counter == 1

    # Invalidate the cache
    invalidated = await cache_worker.invalidate_cache(cached_task, 12)
    assert invalidated is True

    # Call again - should execute the task again
    task_id2 = await cache_worker.add_task(cached_task, 12)
    await asyncio.sleep(0.5)
    assert call_counter == 2  # Should have executed again


@pytest.mark.asyncio
async def test_cache_ttl():
    """Test that cache entries expire"""
    global call_counter
    call_counter = 0

    # Create worker with very short TTL
    worker = AsyncTaskWorker(
        max_workers=2,
        cache_enabled=True,
        cache_ttl=1  # 1 second TTL
    )
    await worker.start()

    try:
        # Make a call to cache the result
        task_id1 = await worker.add_task(cached_task, 15)
        await asyncio.sleep(0.5)
        assert call_counter == 1

        # Call again immediately - should use cache
        task_id2 = await worker.add_task(cached_task, 15)
        await asyncio.sleep(0.5)
        assert call_counter == 1  # No new call

        # Wait for TTL to expire
        await asyncio.sleep(2)

        # Call again - should execute the task again due to TTL expiry
        task_id3 = await worker.add_task(cached_task, 15)
        await asyncio.sleep(0.5)
        assert call_counter == 2  # Should execute again
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_cache_size_eviction():
    """Test that cache evicts items when size limit is reached"""
    global call_counter
    call_counter = 0

    # Create debug adapter for testing
    debug_adapter = MemoryCacheAdapter(max_size=3)  # Only 3 entries

    # Create worker with our debug adapter
    worker = AsyncTaskWorker(
        max_workers=2,
        cache_enabled=True,
        cache_adapter=debug_adapter
    )
    await worker.start()

    try:
        # Step 1: Fill the cache with 3 entries
        for i in range(3):
            task_id = await worker.add_task(cached_task, i)
            await asyncio.sleep(0.3)

        assert call_counter == 3

        # Verify all 3 entries are cached
        assert len(debug_adapter._cache) == 3

        # Step 2: Add a new item to trigger eviction
        task_id = await worker.add_task(cached_task, 100)
        await asyncio.sleep(0.3)

        # Should have executed the task once more
        assert call_counter == 4

        # Cache should still have 3 items (max size)
        assert len(debug_adapter._cache) == 3

        # Generate cache keys for all values
        cache_keys = {}
        for i in [0, 1, 2, 100]:
            # We need to manually create the same key format used by TaskCache
            try:
                import json
                import hashlib
                key_parts = [cached_task.__name__, json.dumps((i,)), json.dumps({})]
                key_string = ":".join(key_parts)
                cache_keys[i] = hashlib.md5(key_string.encode()).hexdigest()
            except Exception:
                # Fallback method
                cache_keys[i] = f"key_for_{i}"

        # Make sure the newest item (100) is in the cache
        assert cache_keys[100] in debug_adapter._cache

        # Only one of the original items should be missing (evicted)
        evicted_count = 0
        for i in range(3):
            if cache_keys[i] not in debug_adapter._cache:
                evicted_count += 1

        assert evicted_count == 1, f"Expected exactly one eviction, got {evicted_count}"
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_clear_cache(cache_worker):
    """Test clearing the entire cache"""
    global call_counter
    call_counter = 0

    # Make several calls to populate cache
    for i in range(3):
        task_id = await cache_worker.add_task(cached_task, i)
        await asyncio.sleep(0.5)

    assert call_counter == 3

    # Verify they're cached
    for i in range(3):
        task_id = await cache_worker.add_task(cached_task, i)
        await asyncio.sleep(0.2)

    # Counter shouldn't increase
    assert call_counter == 3

    # Clear the cache
    await cache_worker.clear_cache()

    # Calls should execute again
    for i in range(3):
        task_id = await cache_worker.add_task(cached_task, i)
        await asyncio.sleep(0.5)

    assert call_counter == 6


@pytest.mark.asyncio
async def test_custom_ttl(cache_worker):
    """Test setting custom TTL per task"""
    global call_counter
    call_counter = 0

    # Call with short TTL override
    task_id1 = await cache_worker.add_task(cached_task, 50, cache_ttl=1)
    await asyncio.sleep(0.5)
    assert call_counter == 1

    # Verify it's cached
    task_id2 = await cache_worker.add_task(cached_task, 50)
    await asyncio.sleep(0.2)
    assert call_counter == 1  # Still 1

    # Wait for custom TTL to expire
    await asyncio.sleep(2)

    # Should execute again
    task_id3 = await cache_worker.add_task(cached_task, 50)
    await asyncio.sleep(0.5)
    assert call_counter == 2
