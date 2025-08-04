"""
Test the AsyncCacheAdapter that bridges between AsyncTaskWorker and AsyncCache.
"""

import pytest

from async_task_worker.adapters.cache_adapter import AsyncCacheAdapter


@pytest.fixture
async def cache_adapter():
    """Create a cache adapter for testing."""
    adapter = AsyncCacheAdapter(default_ttl=30, enabled=True, max_size=100, cleanup_interval=60)

    # Start cleanup task
    await adapter.start_cleanup_task()

    # Yield for test use
    yield adapter

    # Clean up
    await adapter.stop_cleanup_task()
    await adapter.clear()


@pytest.mark.asyncio
async def test_adapter_basic_operations(cache_adapter):
    """Test basic cache operations via the adapter."""
    # Set a value
    success = await cache_adapter.set("test_func", args=(1, 2), kwargs={"a": "b"}, result="test_result")
    assert success is True

    # Get the value
    hit, result = await cache_adapter.get("test_func", args=(1, 2), kwargs={"a": "b"})
    assert hit is True
    assert result == "test_result"

    # Invalidate
    success = await cache_adapter.invalidate("test_func", args=(1, 2), kwargs={"a": "b"})
    assert success is True

    # Verify it's gone
    hit, _ = await cache_adapter.get("test_func", args=(1, 2), kwargs={"a": "b"})
    assert hit is False


@pytest.mark.asyncio
async def test_task_id_operations(cache_adapter):
    """Test task_id based operations."""
    # Set with task_id
    task_id = "test-task-123"
    await cache_adapter.set("task_func", args=(), kwargs={}, result="task_result", task_id=task_id)

    # Get with same task_id
    hit, result = await cache_adapter.get("task_func", args=(), kwargs={}, task_id=task_id)
    assert hit is True
    assert result == "task_result"

    # Get by task_id directly
    hit, direct_result = await cache_adapter.get_by_task_id(task_id)
    assert hit is True
    assert direct_result == "task_result"

    # Invalidate by task_id
    success = await cache_adapter.invalidate_by_task_id(task_id)
    assert success is True

    # Verify it's gone
    hit, _ = await cache_adapter.get("task_func", args=(), kwargs={}, task_id=task_id)
    assert hit is False

    # Verify direct access also fails
    hit, _ = await cache_adapter.get_by_task_id(task_id)
    assert hit is False


@pytest.mark.asyncio
async def test_adapter_property_accessors(cache_adapter):
    """Test property accessors and modifiers."""
    # Test enabled property
    assert cache_adapter.enabled is True
    cache_adapter.enabled = False
    assert cache_adapter.enabled is False

    # Test default_ttl property
    assert cache_adapter.default_ttl == 30
    cache_adapter.default_ttl = 60
    assert cache_adapter.default_ttl == 60


@pytest.mark.asyncio
async def test_custom_key_functions():
    """Test using custom key functions with the adapter."""
    adapter = AsyncCacheAdapter(enabled=True)

    try:
        from async_cache import compose_key_functions, extract_key_component

        # Create custom key function
        key_fn = compose_key_functions(
            extract_key_component("kwargs.user_id"), extract_key_component("metadata.version")
        )

        # Set with custom key function and metadata
        await adapter.set(
            "get_user",
            args=(),
            kwargs={"user_id": 123},
            result={"name": "Test"},
            metadata={"version": "2.0"},
            cache_key_fn=key_fn,
        )

        # Get with same parameters
        hit, result = await adapter.get(
            "get_user", args=(), kwargs={"user_id": 123}, metadata={"version": "2.0"}, cache_key_fn=key_fn
        )

        assert hit is True
        assert result == {"name": "Test"}

        # Get with different parameters should miss
        hit, _ = await adapter.get(
            "get_user",
            args=(),
            kwargs={"user_id": 123},
            metadata={"version": "3.0"},  # Different version
            cache_key_fn=key_fn,
        )

        assert hit is False

    finally:
        # Clean up
        await adapter.stop_cleanup_task()
        await adapter.clear()
